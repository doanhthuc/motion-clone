import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite struct SpendGateTests {
    /// Scripted answers, one per request, in order.
    final class Script: @unchecked Sendable {
        private let lock = NSLock()
        private var answers: [(Int, [String: String], Data)]
        init(_ answers: [(Int, [String: String], Data)]) { self.answers = answers }
        func next() -> (Int, [String: String], Data) {
            lock.withLock { answers.isEmpty ? (500, [:], Data()) : answers.removeFirst() }
        }
    }

    final class Box<T: Sendable>: @unchecked Sendable {
        private let lock = NSLock()
        private var stored: T
        init(_ v: T) { stored = v }
        var value: T { lock.withLock { stored } }
        func mutate(_ f: (inout T) -> Void) { lock.withLock { f(&stored) } }
    }

    static let accepted = TestSupport.json(#"{"run_id":"tg-1000","outcome":"started"}"#, status: 202)
    static let busy = TestSupport.json(#"{"error":{"code":"bot_busy","message":"the bot is busy"}}"#, status: 503)
    static let unknown = TestSupport.json(#"{"error":{"code":"outcome_unknown","message":"did not finish"}}"#, status: 409)
    static let stale = TestSupport.json(#"{"error":{"code":"stale_panel","message":"the job changed since the panel was read — read it again"}}"#, status: 409)
    static let choice = TestSupport.json(#"{"error":{"code":"choice_required","message":"try-on already ran"},"panel_token":"55.6"}"#, status: 409)
    static let cloudflare524: (Int, [String: String], Data) = (524, ["Content-Type": "text/html"], Data("<html>timeout</html>".utf8))
    static let dropped: (Int, [String: String], Data) = (-1, [:], Data())

    struct Harness {
        let gate: SpendGate
        let ledger: IdempotencyLedger
        let sleeps: Box<[Duration]>
        let minted: Box<Int>
    }

    static func harness(now: Date = Date(timeIntervalSince1970: 1_790_000_000)) -> Harness {
        let ledger = IdempotencyLedger(root: FileManager.default.temporaryDirectory
            .appending(component: "gate-\(UUID().uuidString)"))
        let sleeps = Box<[Duration]>([])
        let minted = Box(0)
        let gate = SpendGate(
            client: TestSupport.client(), ledger: ledger,
            sleep: { d in sleeps.mutate { $0.append(d) } },
            now: { now },
            makeKey: { minted.mutate { $0 += 1 }; return "KEY-\(minted.value)" })
        return Harness(gate: gate, ledger: ledger, sleeps: sleeps, minted: minted)
    }

    static var keys: [String?] {
        StubURLProtocol.requests.map { $0.value(forHTTPHeaderField: "Idempotency-Key") }
    }

    @Test func ledgerIsOnDiskBeforeTheRequestArrives() async throws {
        let h = Self.harness()
        let sawEntry = Box(false)
        let file = h.ledger.fileURL
        StubURLProtocol.install { _ in
            sawEntry.mutate { $0 = FileManager.default.fileExists(atPath: file.path) }
            return Self.accepted
        }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(sawEntry.value)
        #expect(result == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(try h.ledger.load() == nil)
    }

    @Test func droppedConnectionIsResentWithTheSameKey() async throws {
        let h = Self.harness()
        let script = Script([Self.dropped, Self.accepted])
        StubURLProtocol.install { _ in script.next() }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(result == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(Self.keys == ["KEY-1", "KEY-1"])
        #expect(h.sleeps.value == [.seconds(2)])
        #expect(h.minted.value == 1)
    }

    @Test func unreachableKeepsTheLedgerAndRecheckReusesTheKey() async throws {
        let h = Self.harness()
        let script = Script([Self.dropped, Self.cloudflare524, Self.dropped, Self.accepted])
        StubURLProtocol.install { _ in script.next() }
        let first = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        guard case .unreachable = first else { Issue.record("got \(first)"); return }
        #expect(try h.ledger.load()?.key == "KEY-1")
        #expect(h.sleeps.value == [.seconds(2), .seconds(5)])
        let second = await h.gate.recheck { _, _ in }
        #expect(second == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(Self.keys == ["KEY-1", "KEY-1", "KEY-1", "KEY-1"])
        #expect(h.minted.value == 1)
        #expect(try h.ledger.load() == nil)
    }

    @Test func botBusyRetriesThreeTimesThenGivesUpAndClears() async throws {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.busy }
        let retries = Box<[Int]>([])
        let result = await h.gate.perform(.phaseA, label: "Preview") { n, reason in
            #expect(reason == .botBusy)
            retries.mutate { $0.append(n) }
        }
        #expect(result == .busy(attempts: 4))
        #expect(Self.keys == ["KEY-1", "KEY-1", "KEY-1", "KEY-1"])
        #expect(h.sleeps.value == [.seconds(5), .seconds(5), .seconds(5)])
        #expect(retries.value == [1, 2, 3])
        #expect(try h.ledger.load() == nil)
    }

    @Test func botBusyThenAcceptedKeepsTheKey() async {
        let h = Self.harness()
        let script = Script([Self.busy, Self.accepted])
        StubURLProtocol.install { _ in script.next() }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(result == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(Self.keys == ["KEY-1", "KEY-1"])
    }

    @Test func outcomeUnknownIsNeverRetried() async throws {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.unknown }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(result == .outcomeUnknown)
        #expect(StubURLProtocol.requests.count == 1)
        #expect(try h.ledger.load() == nil)
    }

    static let internal500 = TestSupport.json(#"{"error":{"code":"internal","message":"internal error"}}"#, status: 500)
    static let upstream502 = TestSupport.json(#"{"error":{"code":"upstream_unavailable","message":"RunPod did not answer"}}"#, status: 502)
    static let draft422 = TestSupport.json(#"{"error":{"code":"draft_invalid","message":"the draft is missing a driver"}}"#, status: 422)

    /// `server.py` answers an unhandled exception with `500 internal` after
    /// `idem.begin` wrote `pending`, so the resend finds the pending record.
    @Test func jsonServerErrorIsResentAndFindsOutcomeUnknown() async throws {
        let h = Self.harness()
        let script = Script([Self.internal500, Self.unknown])
        StubURLProtocol.install { _ in script.next() }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(result == .outcomeUnknown)
        #expect(Self.keys == ["KEY-1", "KEY-1"])
        #expect(h.sleeps.value == [.seconds(2)])
        #expect(try h.ledger.load() == nil)
    }

    @Test func repeatedJsonServerErrorIsTheStoredAnswer() async throws {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.upstream502 }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(result == .refused(status: 502, code: "upstream_unavailable",
                                   message: "RunPod did not answer", panelToken: nil))
        #expect(StubURLProtocol.requests.count == 2)
        #expect(Self.keys == ["KEY-1", "KEY-1"])
        #expect(try h.ledger.load() == nil)
    }

    @Test func json422IsDefinitiveAfterOneRequest() async throws {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.draft422 }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(result == .refused(status: 422, code: "draft_invalid",
                                   message: "the draft is missing a driver", panelToken: nil))
        #expect(StubURLProtocol.requests.count == 1)
        #expect(try h.ledger.load() == nil)
    }

    @Test func jsonServerErrorThenAcceptedKeepsTheKey() async throws {
        let h = Self.harness()
        let script = Script([Self.internal500, Self.accepted])
        StubURLProtocol.install { _ in script.next() }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(result == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(Self.keys == ["KEY-1", "KEY-1"])
        #expect(h.minted.value == 1)
        #expect(try h.ledger.load() == nil)
    }

    @Test func differingJsonServerErrorsEndUnreachableWithTheLedgerKept() async throws {
        let h = Self.harness()
        let script = Script([Self.internal500, Self.upstream502, Self.dropped])
        StubURLProtocol.install { _ in script.next() }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        guard case .unreachable = result else { Issue.record("got \(result)"); return }
        #expect(StubURLProtocol.requests.count == 3)
        #expect(try h.ledger.load()?.key == "KEY-1")
    }

    @Test func replayTreatsAJsonServerErrorAsUnreachable() async throws {
        let h = Self.harness()
        try h.ledger.save(SpendLedgerEntry(key: "OLD", intent: .phaseA, label: "Preview",
                                           createdAt: Date(timeIntervalSince1970: 1_790_000_000 - 60)))
        StubURLProtocol.install { _ in Self.internal500 }
        let result = await h.gate.replayPending()
        guard case .unreachable = result else { Issue.record("got \(String(describing: result))"); return }
        #expect(Self.keys == ["OLD"])
        #expect(try h.ledger.load()?.key == "OLD")
    }

    @Test func stalePanelIsDefinitiveAndSentOnce() async {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.stale }
        let intent = SpendIntent.confirm(runID: "tg-1000", provider: .runpod, panelToken: "old",
                                         gpu: nil, tryon: nil)
        let result = await h.gate.perform(intent, label: "Confirm") { _, _ in }
        #expect(result == .refused(status: 409, code: "stale_panel",
                                   message: "the job changed since the panel was read — read it again",
                                   panelToken: nil))
        #expect(StubURLProtocol.requests.count == 1)
    }

    @Test func choiceRequiredCarriesTheFreshPanelToken() async {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.choice }
        let intent = SpendIntent.confirm(runID: "tg-1000", provider: .runpod, panelToken: "old",
                                         gpu: nil, tryon: nil)
        let result = await h.gate.perform(intent, label: "Confirm") { _, _ in }
        #expect(result == .refused(status: 409, code: "choice_required",
                                   message: "try-on already ran", panelToken: "55.6"))
    }

    @Test func cloudflareHTML403IsDefinitiveAccessDenied() async throws {
        let h = Self.harness()
        StubURLProtocol.install { _ in (403, ["Content-Type": "text/html"], Data(Fixtures.cloudflareHTML.utf8)) }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        guard case .refused(403, "access_denied", _, nil) = result else { Issue.record("got \(result)"); return }
        #expect(try h.ledger.load() == nil)
    }

    @Test func performRefusesWhileAnEntryIsPending() async throws {
        let h = Self.harness()
        try h.ledger.save(SpendLedgerEntry(key: "OLD", intent: .phaseA, label: "Preview",
                                           createdAt: Date(timeIntervalSince1970: 1_790_000_000 - 60)))
        StubURLProtocol.install { _ in Self.accepted }
        let result = await h.gate.perform(.phaseA, label: "Preview again") { _, _ in }
        guard case .notSent = result else { Issue.record("got \(result)"); return }
        #expect(StubURLProtocol.requests.isEmpty)
        #expect(h.minted.value == 0)
    }

    @Test func replayResendsAYoungEntryOnceWithItsKey() async throws {
        let h = Self.harness()
        try h.ledger.save(SpendLedgerEntry(key: "OLD", intent: .phaseA, label: "Preview",
                                           createdAt: Date(timeIntervalSince1970: 1_790_000_000 - 3600)))
        StubURLProtocol.install { _ in Self.dropped }
        let result = await h.gate.replayPending()
        guard case .unreachable = result else { Issue.record("got \(String(describing: result))"); return }
        #expect(Self.keys == ["OLD"])          // a single attempt, no retry loop
        #expect(try h.ledger.load()?.key == "OLD")
        #expect(h.minted.value == 0)
    }

    @Test func replayNeverResendsAnEntryTwentyHoursOld() async throws {
        let h = Self.harness()
        let created = Date(timeIntervalSince1970: 1_790_000_000 - 20 * 3600)
        try h.ledger.save(SpendLedgerEntry(key: "OLD", intent: .phaseA, label: "Confirm", createdAt: created))
        StubURLProtocol.install { _ in Self.accepted }
        let result = await h.gate.replayPending()
        #expect(result == .expired(label: "Confirm", createdAt: created))
        #expect(StubURLProtocol.requests.isEmpty)
        #expect(try h.ledger.load() == nil)
    }

    @Test func replayWithNothingPendingIsNil() async {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.accepted }
        #expect(await h.gate.replayPending() == nil)
        #expect(StubURLProtocol.requests.isEmpty)
    }

    @Test func corruptLedgerIsReportedNotResent() async throws {
        let h = Self.harness()
        try FileManager.default.createDirectory(at: h.ledger.root, withIntermediateDirectories: true)
        try Data("{".utf8).write(to: h.ledger.fileURL)
        StubURLProtocol.install { _ in Self.accepted }
        let result = await h.gate.replayPending()
        #expect(result == .expired(label: "An earlier spend", createdAt: nil))
        #expect(StubURLProtocol.requests.isEmpty)
    }

    @Test func concurrentPerformIsRefusedWhileOneIsSending() async throws {
        let h = Self.harness()
        let release = DispatchSemaphore(value: 0)
        StubURLProtocol.install { _ in release.wait(); return Self.accepted }
        let first = Task { await h.gate.perform(.phaseA, label: "Preview") { _, _ in } }
        while StubURLProtocol.requests.isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        let second = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        guard case .notSent = second else { Issue.record("got \(second)"); return }
        release.signal()
        #expect(await first.value == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(StubURLProtocol.requests.count == 1)
    }
}
}
