import MotionKit
import SwiftUI

/// Phase A, running and finished, as one look per page. The 2026-09-26 list
/// put each preview in a grouped section with five text rows under it, so two
/// looks were a long scroll, and while Phase A ran a job was one empty circle
/// and a file name. Here a swipe moves between looks, the actions sit in one
/// bar under the page, and a job still generating shows its own photos being
/// worked on.
struct TryonCarousel: View {
    let flow: RunFlow
    @State private var selection: String?

    private var pages: [TryonPreview] { flow.cards }
    private var current: TryonPreview? {
        pages.first { $0.id == selection } ?? pages.first
    }

    var body: some View {
        VStack(spacing: 14) {
            if pages.isEmpty {
                GeneratingCanvas(flow: flow, preview: nil)
                    .padding(.horizontal, 20)
            } else {
                pager
                StatusDots(items: pages.map { .init(id: $0.id, status: $0.status) },
                           noun: "Look", selection: current?.id) { id in
                    withAnimation(.snappy) { selection = id }
                }
            }
            if let current {
                TryonActionBar(flow: flow, preview: current)
                    .padding(.horizontal, 20)
            }
            footer.padding(.horizontal, 20)
        }
        .padding(.top, 8)
        .padding(.bottom, 12)
        .onChange(of: pages.map(\.id)) { _, ids in
            // A drop removes the page under the finger; land on a neighbour.
            if let selection, !ids.contains(selection) { self.selection = ids.first }
        }
    }

    private var pager: some View {
        ScrollView(.horizontal) {
            LazyHStack(spacing: 12) {
                ForEach(pages) { preview in
                    TryonPage(flow: flow, preview: preview)
                        .containerRelativeFrame(.horizontal)
                        .id(preview.id)
                }
            }
            .scrollTargetLayout()
        }
        // The neighbours peek in at the edges, so the page reads as swipeable
        // without a hint.
        .contentMargins(.horizontal, 24, for: .scrollContent)
        .scrollTargetBehavior(.viewAligned)
        .scrollPosition(id: $selection)
        .scrollIndicators(.hidden)
    }

    @ViewBuilder private var footer: some View {
        if flow.phase == .previews {
            Button {
                Task { await flow.continueToRent() }
            } label: {
                Label("Continue to rent", systemImage: "arrow.right")
                    .labelStyle(TrailingIconLabelStyle())
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(flow.isSpending)
        } else {
            let done = pages.filter { $0.status == .done }.count
            HStack(spacing: 8) {
                PulseDot(color: Theme.accent, size: 6)
                Text(pages.isEmpty ? "Starting try-on on the VPS…"
                                   : "\(done) of \(pages.count) looks ready · no GPU rented yet")
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(Theme.secondary)
            }
            .frame(maxWidth: .infinity, minHeight: 50)
        }
    }
}

private struct TrailingIconLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 8) { configuration.title; configuration.icon }
    }
}

// MARK: - one page

private struct TryonPage: View {
    let flow: RunFlow
    let preview: TryonPreview
    @Environment(AppModel.self) private var model
    @State private var image: UIImage?
    @State private var revealed = false
    @State private var original: UIImage?
    @State private var comparing = false
    @State private var showVersions = false

    var body: some View {
        ZStack {
            if let image {
                result(image)
            } else if preview.status == .error {
                failed
            } else {
                // `hasImage` with no bytes yet is a download, not a generation,
                // but the canvas is the right thing to look at for that second too.
                GeneratingCanvas(flow: flow, preview: preview)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .clipShape(.rect(cornerRadius: 22))
        .overlay(alignment: .bottom) { caption }
        .overlay(alignment: .top) { badges }
        .contentShape(.rect(cornerRadius: 22))
        // A swipe up opens the earlier versions. Simultaneous, so the pager's
        // sideways drag is never blocked by it.
        .simultaneousGesture(
            DragGesture(minimumDistance: 24).onEnded { value in
                guard image != nil,
                      value.translation.height < -60,
                      abs(value.translation.width) < abs(value.translation.height) / 2 else { return }
                showVersions = true
            }
        )
        .sheet(isPresented: $showVersions) {
            VersionsSheet(flow: flow, preview: preview)
        }
        .task(id: "\(preview.index)#\(flow.imageGeneration)#\(preview.hasImage)") {
            guard preview.hasImage else { image = nil; revealed = false; return }
            let loaded = await flow.image(index: preview.index).flatMap(UIImage.init(data:))
            image = loaded
            withAnimation(.easeOut(duration: 0.9)) { revealed = loaded != nil }
        }
    }

    private func result(_ image: UIImage) -> some View {
        // `Color.clear` takes the page's size; a bare `scaledToFill` in a ZStack
        // grows the stack past it and crops the fitted image (2026-09-26).
        Color.clear
            .overlay {
                // The same frame, blurred, fills the bars a portrait image leaves.
                Image(uiImage: image).resizable().scaledToFill()
                    .blur(radius: 40).opacity(0.55)
            }
            .overlay {
                Image(uiImage: comparing ? (original ?? image) : image)
                    .resizable().scaledToFit()
                    .blur(radius: revealed ? 0 : 24)
                    .scaleEffect(revealed ? 1 : 1.04)
                    .contentTransition(.opacity)
            }
            .clipped()
        .animation(.easeInOut(duration: 0.18), value: comparing)
        // Press and hold shows the character photo the look was made from.
        .onLongPressGesture(minimumDuration: 0.15, maximumDistance: 12) {
        } onPressingChanged: { pressing in
            comparing = pressing && original != nil
            if pressing { UIImpactFeedbackGenerator(style: .light).impactOccurred() }
        }
        .task(id: preview.id) {
            original = await model.inputImage(flow.inputMaterialID(for: preview, role: .character), full: true)
        }
        .accessibilityElement()
        .accessibilityLabel("Try-on for \(preview.run)")
        .accessibilityHint("Touch and hold to compare with the original photo. Swipe up for earlier versions.")
    }

    private var failed: some View {
        VStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 34)).foregroundStyle(Theme.danger)
            Text("Try-on failed").font(.headline)
            Text("Regenerate below, or drop this look from the batch.")
                .font(.subheadline).foregroundStyle(Theme.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.surface)
    }

    private var caption: some View {
        VStack(alignment: .leading, spacing: 3) {
            if comparing {
                Label("Original", systemImage: "person.crop.rectangle")
                    .font(.footnote.weight(.semibold))
            }
            Text(preview.run).font(.subheadline.weight(.semibold))
                .lineLimit(1).truncationMode(.middle)
            if let shares = preview.shares, !shares.isEmpty {
                Text("Used by \(shares.count + 1) videos").font(.footnote).foregroundStyle(.white.opacity(0.75))
            }
        }
        .foregroundStyle(.white)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16).padding(.top, 36).padding(.bottom, 14)
        .background(LinearGradient(colors: [.clear, .black.opacity(0.7)], startPoint: .top, endPoint: .bottom))
        .allowsHitTesting(false)
    }

    private var badges: some View {
        HStack(spacing: 6) {
            if flow.isSeeded(preview) {
                Label("Saved try-on", systemImage: "photo.badge.checkmark")
                    .accessibilityIdentifier("tryon.seeded.\(preview.index)")
            }
            if flow.isKept(preview.index) {
                Label("In library", systemImage: "heart.fill")
            }
            Spacer(minLength: 0)
            if image != nil, !comparing {
                Label("Hold to compare", systemImage: "hand.tap")
                    .foregroundStyle(.white.opacity(0.8))
            }
        }
        .font(.caption.weight(.semibold))
        .labelStyle(.titleAndIcon)
        .foregroundStyle(.white)
        .padding(12)
        .allowsHitTesting(false)
    }
}

// MARK: - generating

/// A job Phase A has not finished: its character photo, dimmed, with a band
/// of light sweeping down it and the outfit waiting in the corner. Pending
/// jobs hold still — only the one the VPS is working on moves.
struct GeneratingCanvas: View {
    let flow: RunFlow
    let preview: TryonPreview?
    @Environment(AppModel.self) private var model
    @State private var character: UIImage?
    @State private var outfit: UIImage?
    @State private var shownAt = Date.now

    /// A done job whose image is still downloading counts as running.
    private var running: Bool {
        guard let preview else { return true }
        return preview.status == .running || preview.status == .done
    }

    var body: some View {
        WorkCanvas(backdrop: character, inset: outfit, animating: running) {
            WorkStatus(title: running ? "Dressing…" : "Queued", active: running) {
                if running {
                    TimelineView(.periodic(from: shownAt, by: 1)) { ctx in
                        Text(Format.clock(ctx.date.timeIntervalSince(shownAt)))
                    }
                } else {
                    Text("Waits for the look before it")
                }
            }
        }
        .task(id: preview?.id) {
            guard let preview else { return }
            async let c = model.inputImage(flow.inputMaterialID(for: preview, role: .character))
            async let o = model.inputImage(flow.inputMaterialID(for: preview, role: .outfit))
            let (ci, oi) = await (c, o)
            withAnimation(.easeOut(duration: 0.4)) { character = ci; outfit = oi }
        }
    }
}

// MARK: - actions

/// The current look's actions. Destructive and rarely used ones live in the
/// ⋯ menu so the bar stays one row.
private struct TryonActionBar: View {
    let flow: RunFlow
    let preview: TryonPreview
    @State private var showRegenerate = false
    @State private var showVersions = false
    @State private var confirmDrop = false
    @State private var editing: StudioRef?

    var body: some View {
        HStack(spacing: 8) {
            let kept = flow.isKept(preview.index)
            action(kept ? "Kept" : "Keep", kept ? "heart.fill" : "heart",
                   tint: kept ? Theme.accent : Theme.label) {
                Task { await flow.keep(index: preview.index) }
            }
            .disabled(!preview.hasImage || kept)

            action("Regenerate", "arrow.clockwise") { showRegenerate = true }
                .disabled(!flow.canSpend)

            action("Studio", "wand.and.stars") {
                if let runID = flow.runID {
                    editing = StudioRef(kind: .runTryon, id: "\(runID)/\(preview.index)")
                }
            }
            .disabled(!preview.hasImage || flow.runID == nil)

            Menu {
                Button("Earlier versions", systemImage: "square.stack") { showVersions = true }
                    .disabled(!preview.hasImage)
                // `canDropFromBatch` goes false while a drop is in flight, so the
                // item stays listed but inert until the trailing refreshes land.
                if flow.canDropFromBatch(preview) || flow.isDropping {
                    Button(flow.isDropping ? "Dropping…" : "Drop from batch",
                           systemImage: "trash", role: .destructive) { confirmDrop = true }
                        .disabled(!flow.canDropFromBatch(preview) || flow.batchEntry(for: preview) == nil)
                        .accessibilityIdentifier("tryon.drop.\(preview.index)")
                }
            } label: {
                ActionLabel(title: "More", systemImage: "ellipsis", tint: Theme.label)
            }
        }
        .sheet(isPresented: $showRegenerate) {
            RegenerateSheet(flow: flow, preview: preview) { showRegenerate = false }
        }
        .sheet(isPresented: $showVersions) { VersionsSheet(flow: flow, preview: preview) }
        .sheet(item: $editing) { EditInStudioSheet(ref: $0) }
        .confirmationDialog("Drop \(preview.run) from the batch?", isPresented: $confirmDrop,
                            titleVisibility: .visible) {
            Button("Drop", role: .destructive) {
                confirmDrop = false
                Task { await flow.drop(preview) }
            }
            Button("Cancel", role: .cancel) { confirmDrop = false }
        } message: {
            Text("Free — nothing is rented yet. The draft is validated again, and Confirm then rents only what is left.")
        }
    }

    private func action(_ title: String, _ systemImage: String, tint: Color = Theme.label,
                        perform: @escaping () -> Void) -> some View {
        Button(action: perform) { ActionLabel(title: title, systemImage: systemImage, tint: tint) }
            .buttonStyle(ActionButtonStyle())
    }
}

// MARK: - sheets

private struct VersionsSheet: View {
    let flow: RunFlow
    let preview: TryonPreview
    @State private var loading = true

    var body: some View {
        let items = flow.versions[preview.index] ?? []
        VStack(alignment: .leading, spacing: 14) {
            Text("Earlier versions").font(.headline)
            if loading && items.isEmpty {
                ProgressView().frame(maxWidth: .infinity, minHeight: 150)
            } else if items.isEmpty {
                Text("No earlier versions — Regenerate keeps the current image as one.")
                    .font(.subheadline).foregroundStyle(Theme.secondary)
                    .frame(maxWidth: .infinity, minHeight: 150, alignment: .topLeading)
            } else {
                ScrollView(.horizontal) {
                    HStack(spacing: 10) {
                        ForEach(Array(items.enumerated()), id: \.offset) { n, data in
                            VStack(spacing: 6) {
                                if let ui = UIImage(data: data) {
                                    Image(uiImage: ui).resizable().scaledToFill()
                                        .frame(width: 112, height: 150)
                                        .clipShape(.rect(cornerRadius: Theme.Radius.small))
                                }
                                Text("v\(n + 1)").font(.footnote.monospacedDigit()).foregroundStyle(Theme.secondary)
                            }
                        }
                    }
                }
                .scrollIndicators(.hidden)
            }
        }
        .padding(20)
        .presentationDetents([.height(260)])
        .presentationDragIndicator(.visible)
        .task {
            await flow.loadVersions(index: preview.index)
            loading = false
        }
    }
}

private struct RegenerateSheet: View {
    let flow: RunFlow
    let preview: TryonPreview
    let close: () -> Void
    @State private var guidance: Set<Guidance> = []

    var body: some View {
        NavigationStack {
            Form {
                Section("Guidance") {
                    ForEach(Guidance.allCases, id: \.self) { g in
                        Toggle(g.label, isOn: Binding(
                            get: { guidance.contains(g) },
                            set: { on in if on { guidance.insert(g) } else { guidance.remove(g) } }))
                    }
                }
                Section {
                    Text("Spends Gemini/Qwen quota again. The current image becomes an earlier version.")
                        .font(.footnote).foregroundStyle(Theme.secondary)
                }
            }
            .navigationTitle("Regenerate #\(preview.index)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel", action: close) }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Regenerate") {
                        close()
                        let chosen = guidance
                        Task { await flow.regenerate(index: preview.index, guidance: chosen) }
                    }
                    .disabled(!flow.canSpend)
                }
            }
        }
        .presentationDetents([.medium])
    }
}

// MARK: - input photos

extension AppModel {
    /// A job's input photo, by material id. The thumbnail is enough behind the
    /// generating canvas; the press-to-compare original wants the full file.
    func inputImage(_ materialID: String?, full: Bool = false) async -> UIImage? {
        guard let materialID, let store = materials else { return nil }
        if !store.loaded { await store.refresh() }
        guard let material = store.materials.first(where: {
            $0.id == materialID || "\($0.owner)/\($0.name)" == materialID
        }) else { return nil }
        if full, let data = try? await store.client.data("v1", "materials", material.owner, material.name),
           let image = UIImage(data: data) {
            return image
        }
        return await store.thumbnail(for: material).flatMap(UIImage.init(data:))
    }
}
