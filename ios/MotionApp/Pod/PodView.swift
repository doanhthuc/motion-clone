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
    @State private var watchLevel = WatchDrawer.Level.collapsed

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
            .overlay(alignment: .bottom) {
                ZStack(alignment: .bottom) {
                    if watchLevel == .open {
                        Color.black.opacity(0.35)
                            .contentShape(.rect)
                            .onTapGesture { withAnimation(.snappy) { watchLevel = .collapsed } }
                            .transition(.opacity)
                            .accessibilityLabel("Close the watch list")
                            .accessibilityAddTraits(.isButton)
                            .accessibilityIdentifier("pod.watchScrim")
                    }
                    WatchDrawer(subs: subs, level: $watchLevel)
                        .frame(maxHeight: watchLevel == .open ? proxy.size.height * 0.7 : nil, alignment: .bottom)
                        .padding(.horizontal, 12)
                }
            }
        }
        // The fired banner now lives in RootView (every tab, 2026-09-27); it
        // reads these two to stay hidden behind an open drawer and to open it.
        .onChange(of: watchLevel, initial: true) { _, level in model.watchDrawerOpen = level == .open }
        .onChange(of: model.watchDrawerRequested, initial: true) { _, requested in
            guard requested else { return }
            model.watchDrawerRequested = false
            withAnimation(.snappy) { watchLevel = .open }
        }
        .navigationTitle("Pod")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { moreMenu } }
        .sheet(isPresented: $showBalance) { BalanceSheet(store: balance) }
        .sheet(item: $openGpu) { target in
            if let stock = gpu.stock {
                GpuSheet(gpu: target.gpu, stock: stock, subs: subs, gpuStore: gpu, pod: pod,
                         spending: flow.isSpending,
                         onMigrate: { model.migrateSheet = MigrateRequest(destination: $0) })
            }
        }
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
                // Upper clamp 150, not 110: on an iPhone 18 Pro Max's taller stage 110 left
                // ~40% of the screen blank above the drawer band (2026-09-27 review round 1).
                let height = min(max((proxy.size.height - spacing * (rows - 1)) / max(rows, 1), 64), 150)
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
            .accessibilityIdentifier("pod.menu.checkVast")
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
