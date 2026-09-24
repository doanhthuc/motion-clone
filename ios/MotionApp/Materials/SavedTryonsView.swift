import MotionKit
import SwiftUI

struct SavedTryonsView: View {
    let library: TryonLibraryStore
    let materials: MaterialsStore
    let draft: DraftStore
    let composer: BatchComposer
    @Environment(AppModel.self) private var model
    @State private var deleteCandidate: TryonLibraryEntry?

    private let columns = [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Saved try-ons").font(Theme.sans(33, .bold)).foregroundStyle(Theme.ink)
                    Spacer()
                    if library.isStale { StaleTag(lastSuccess: library.lastSuccess) }
                }
                if let message = library.message {
                    MessageCard(text: message) { library.dismissMessage() }
                }
                if library.loaded && library.entries.isEmpty {
                    Text("Nothing saved yet. Tap Keep on a try-on preview to save it here.")
                        .font(Theme.sans(14)).foregroundStyle(Theme.ink2)
                        .frame(maxWidth: .infinity).padding(.vertical, 60)
                        .accessibilityIdentifier("saved.empty")
                } else {
                    LazyVGrid(columns: columns, spacing: 12) {
                        ForEach(library.entries) { entry in
                            SavedTryonTile(entry: entry, library: library, materials: materials,
                                           disabled: draft.isBusy || composer.isRunning,
                                           onUse: { use(entry) }, onDelete: { deleteCandidate = entry })
                        }
                    }
                }
            }
            .padding(.horizontal, 20)
        }
        .refreshable { await library.load() }
        .task {
            await library.load()
            if !materials.loaded { await materials.refresh() }
            if draft.draft == nil { await draft.load() }
        }
        .confirmationDialog(
            "Delete this saved try-on?",
            isPresented: Binding(get: { deleteCandidate != nil }, set: { if !$0 { deleteCandidate = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                guard let entry = deleteCandidate else { return }
                deleteCandidate = nil
                Task { await library.delete(entry) }
            }
            Button("Cancel", role: .cancel) { deleteCandidate = nil }
        } message: {
            let users = deleteCandidate.map { library.users(of: $0.id) } ?? []
            Text(users.isEmpty ? "The image is removed from the VPS."
                 : "Used by \(users.joined(separator: ", ")) — those jobs lose their saved image.")
        }
    }

    private func use(_ entry: TryonLibraryEntry) {
        Task {
            if await library.use(entry) {
                model.newJobMode = .single
                model.selectedTab = .newJob
            }
        }
    }
}

private struct SavedTryonTile: View {
    let entry: TryonLibraryEntry
    let library: TryonLibraryStore
    let materials: MaterialsStore
    /// Both buttons, not just "Use in job". This is the one screen that writes
    /// the draft from a different tab, and a cross build takes minutes — each
    /// step is a `PATCH` whose server-side probe can take ~60 s. A "Use in job"
    /// landing between two steps is merged per role by the server
    /// (`scripts/control/drafts.py:450-455`), so the entry's character stays on
    /// the draft while the next step PATCHes only the outfit and seed: the next
    /// basket job is built from a character the build never chose, with a seed
    /// picked for the original one, and Phase A then skips the provider and
    /// copies a mismatched image (`scripts/batchlib/runner.py:670-685`). Delete
    /// needs it too — a double tap sends two DELETEs and the second surfaces
    /// "That saved try-on was already deleted."
    let disabled: Bool
    let onUse: () -> Void
    let onDelete: () -> Void
    @State private var image: UIImage?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Group {
                if let image {
                    Image(uiImage: image).resizable().scaledToFill()
                } else {
                    // `image(id:)` answers nil for both "this entry has no image"
                    // and "the server was unreachable", so a spinner here would
                    // read as "still loading" and never resolve.
                    Rectangle().fill(Theme.surface2)
                        .overlay(Image(systemName: "photo").foregroundStyle(Theme.ink3))
                }
            }
            .frame(height: 170).frame(maxWidth: .infinity).clipped()
            .clipShape(.rect(cornerRadius: 12))
            Text(name("character") + " · " + name("outfit"))
                .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.ink1).lineLimit(2)
            Text("\(entry.provider) · \(Date(timeIntervalSince1970: entry.savedAt).formatted(date: .abbreviated, time: .omitted))")
                .font(Theme.mono(10)).foregroundStyle(Theme.ink2)
            HStack {
                Button("Use in job", action: onUse)
                    .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                    .accessibilityIdentifier("saved.use.\(entry.id)")
                    .disabled(disabled)
                Spacer()
                Button(role: .destructive, action: onDelete) { Image(systemName: "trash") }
                    .foregroundStyle(Theme.red)
                    .disabled(disabled)
            }
        }
        .padding(10).card()
        .task(id: entry.id) { image = await library.image(id: entry.id).flatMap(UIImage.init(data:)) }
    }

    private func name(_ role: String) -> String {
        guard let id = entry.materialIDs[role] else { return "—" }
        // "(deleted)" means gone, so it is only honest once the list has landed:
        // before that an id can be missing merely because nothing was fetched.
        guard materials.loaded else { return "…" }
        return materials.materials.first { $0.id == id }?.name ?? "(deleted)"
    }
}
