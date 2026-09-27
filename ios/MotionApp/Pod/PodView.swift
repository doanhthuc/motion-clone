import SwiftUI
import MotionKit

/// The Pod tab as one stage that does not scroll (2026-09-27 spec §2): the
/// hero, five GPU tiles sized to the space left, and the Watching drawer
/// below. The old List ran past the screen with the balance, five rows and
/// Move volume stacked; those now live in the hero, the tiles and the ⋯ menu.
struct PodView: View {
    let pod: PodStore
    let gpu: GpuStore
    let balance: BalanceStore
    let flow: RunFlow
    let runs: RunsStore
    let subs: GpuSubsStore
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var openGpu: GpuSheetTarget?
    @State private var showBalance = false

    var body: some View {
        GeometryReader { proxy in
            VStack(alignment: .leading, spacing: 12) {
                PodHero(pod: pod, balance: balance, runs: runs, onBalance: { showBalance = true })
                gpuHeader
                tiles
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, WatchDrawer.collapsedHeight + 8)
            .frame(width: proxy.size.width, height: proxy.size.height, alignment: .top)
        }
        .accessibilityIdentifier("pod.stage")
        .navigationTitle("Pod")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { moreMenu } }
        .sheet(isPresented: $showBalance) { BalanceSheet(store: balance) }
        .task {
            async let a: Void = pod.refresh()
            async let b: Void = balance.load()
            async let c: Void = runs.refresh()
            async let d: Void = subs.load()
            if gpu.stock == nil { await gpu.load() }
            _ = await (a, b, c, d)
        }
        // Scoped to a visible Pod tab in an active scene; cancelled otherwise.
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await subs.load()
            await pod.pollMigration()
        }
    }

    private var gpuHeader: some View {
        HStack {
            Text("GPU").font(.subheadline.weight(.semibold)).foregroundStyle(Theme.secondary)
            if let message = gpu.message {
                Text(message).font(.footnote).foregroundStyle(Theme.warning).lineLimit(1)
            } else if gpu.isStale {
                Text("Couldn't reach runpodctl — last list").font(.footnote).foregroundStyle(Theme.warning).lineLimit(1)
            }
            Spacer()
            Button { Task { await gpu.load(force: true) } } label: {
                ZStack {
                    Image(systemName: "arrow.clockwise").opacity(gpu.isLoading ? 0 : 1)
                    if gpu.isLoading { ProgressView().controlSize(.small) }
                }
                .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.secondary)
                .frame(minWidth: 44, minHeight: 32, alignment: .trailing)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .disabled(gpu.isLoading)
            .accessibilityLabel("Refresh GPU stock")
            .accessibilityIdentifier("gpu.refresh")
        }
    }

    @ViewBuilder private var tiles: some View {
        if let stock = gpu.stock {
            GeometryReader { proxy in
                let spacing: CGFloat = 10
                let rows = CGFloat((stock.gpus.count + 1) / 2)
                let height = min(max((proxy.size.height - spacing * (rows - 1)) / max(rows, 1), 64), 110)
                LazyVGrid(columns: [GridItem(.flexible(), spacing: spacing), GridItem(.flexible(), spacing: spacing)],
                          spacing: spacing) {
                    ForEach(stock.gpus) { row in
                        let watched = subs.watching(gpu: row.gpu)
                        GpuTile(row: row, selected: row.gpu == stock.selected, watching: watched.count,
                                armed: watched.contains { $0.autoResume != nil }, height: height) {
                            openGpu = GpuSheetTarget(gpu: row.gpu)
                        }
                    }
                }
            }
            .opacity(gpu.isStale ? 0.6 : 1)
        } else if let error = gpu.error {
            ErrorBanner(error: error) { await gpu.load() }
        } else {
            LoadingBlock(title: "Reading stock…")
        }
    }

    private var moreMenu: some View {
        Menu {
            Button("Refresh stock", systemImage: "arrow.clockwise") { Task { await gpu.load(force: true) } }
            Button("Move volume…", systemImage: "externaldrive.badge.plus") {
                model.migrateSheet = MigrateRequest(destination: nil)
            }
            .accessibilityIdentifier("pod.moveVolume")
            Button(balance.isLoadingVast ? "Reading Vast credit…" : "Check Vast credit", systemImage: "cloud") {
                showBalance = true
                Task { await balance.loadVast() }
            }
            .disabled(balance.isLoadingVast)
            .accessibilityIdentifier("pod.checkVast")
        } label: {
            Image(systemName: "ellipsis.circle")
        }
        .accessibilityLabel("More")
        .accessibilityIdentifier("pod.more")
    }
}

/// Which GPU the sheet is open for.
struct GpuSheetTarget: Identifiable {
    let gpu: String
    var id: String { gpu }
}
