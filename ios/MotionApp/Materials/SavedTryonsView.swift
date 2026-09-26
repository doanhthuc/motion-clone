import MotionKit
import SwiftUI

struct SavedTryonsView: View {
    let library: TryonLibraryStore
    let materials: MaterialsStore
    let draft: DraftStore
    let composer: BatchComposer
    @Environment(AppModel.self) private var model
    @State private var deleteCandidate: TryonLibraryEntry?
    @State private var editing: StudioRef?

    private let columns = [GridItem(.flexible(), spacing: 12, alignment: .top),
                           GridItem(.flexible(), spacing: 12, alignment: .top)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if library.isStale { StaleTag(lastSuccess: library.lastSuccess) }
                if let message = library.message {
                    MessageCard(text: message) { library.dismissMessage() }.heroSurface()
                }
                if library.loaded && library.entries.isEmpty {
                    EmptyNote(title: "Nothing saved yet", systemImage: "photo.stack",
                              message: "Tap Keep on a try-on preview to save it here.")
                        .padding(.top, 40)
                        .accessibilityIdentifier("saved.empty")
                } else {
                    LazyVGrid(columns: columns, spacing: 20) {
                        ForEach(library.entries) { entry in
                            SavedTryonTile(entry: entry, library: library, materials: materials,
                                           disabled: draft.isBusy || composer.isRunning,
                                           onUse: { use(entry) }, onDelete: { deleteCandidate = entry })
                                .contextMenu {
                                    Button("Edit in Studio", systemImage: "wand.and.stars") {
                                        editing = StudioRef(kind: .tryon, id: entry.id)
                                    }
                                }
                        }
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .background(Theme.bg)
        .refreshable { await library.load() }
        .task {
            await library.load()
            if !materials.loaded { await materials.refresh() }
            if draft.draft == nil { await draft.load() }
        }
        .sheet(item: $editing) { EditInStudioSheet(ref: $0) }
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
            Color.clear
                .aspectRatio(4 / 5, contentMode: .fit)
                .overlay {
                    if let image {
                        Image(uiImage: image).resizable().scaledToFill()
                    } else {
                        // `image(id:)` answers nil for both "this entry has no image"
                        // and "the server was unreachable", so a spinner here would
                        // read as "still loading" and never resolve.
                        Theme.surface.overlay(Image(systemName: "photo").font(.title2)
                            .foregroundStyle(Theme.tertiary))
                    }
                }
                .clipShape(.rect(cornerRadius: Theme.Radius.small))
            VStack(alignment: .leading, spacing: 2) {
                Text(name("character") + " · " + name("outfit"))
                    .font(.subheadline).lineLimit(1).truncationMode(.middle)
                Text("\(entry.provider) · \(Date(timeIntervalSince1970: entry.savedAt).formatted(date: .abbreviated, time: .omitted))")
                    .font(.footnote).foregroundStyle(Theme.secondary)
            }
            HStack {
                Button("Use in job", action: onUse)
                    .font(.subheadline.weight(.semibold))
                    .accessibilityIdentifier("saved.use.\(entry.id)")
                    .disabled(disabled)
                Spacer()
                Button(role: .destructive, action: onDelete) { Image(systemName: "trash") }
                    .foregroundStyle(Theme.secondary)
                    .accessibilityLabel("Delete")
                    .disabled(disabled)
            }
            .frame(minHeight: 44)
        }
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
