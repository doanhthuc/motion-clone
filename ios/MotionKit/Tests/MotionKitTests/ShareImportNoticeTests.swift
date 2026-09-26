import Testing
@testable import MotionKit

@Suite struct ShareImportNoticeTests {
    @Test func downloadingSaysWhereItWillLand() {
        let notice = ShareImportNotice(.downloading)
        #expect(notice.title == "Downloading TikTok video…")
        #expect(notice.body.contains("Materials"))
    }

    @Test func doneShowsTheClipsLengthOnly() {
        let notice = ShareImportNotice(.done(durationS: 14.2))
        #expect(notice.title == "TikTok video added")
        #expect(notice.body == "14s · Ready in Materials.")
    }

    @Test func doneWithoutAProbeStillSaysWhereItIs() {
        #expect(ShareImportNotice(.done(durationS: nil)).body == "Ready in Materials.")
        #expect(ShareImportNotice(.done(durationS: 75)).body == "1:15 · Ready in Materials.")
    }

    @Test func failedCarriesTheServerMessage() {
        let notice = ShareImportNotice(.failed("couldn't download that TikTok video: 403"))
        #expect(notice.title == "TikTok download failed")
        #expect(notice.body == "couldn't download that TikTok video: 403")
    }

    @Test func stillRunningIsNotAFailure() {
        let notice = ShareImportNotice(.stillRunning)
        #expect(notice.title == "Still downloading")
        #expect(!notice.title.lowercased().contains("fail"))
    }
}
