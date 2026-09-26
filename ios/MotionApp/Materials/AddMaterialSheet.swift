import MotionKit
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

/// Adds material from the Materials screen (the category is chosen in the
/// sheet) or from one category's See all (the category is that one).
///
/// Photos and Files are presented by the screen after this sheet has closed,
/// never stacked on top of it. Stacked on the small-detent sheet, the Photos
/// picker was slow to open and flickered on open and again on the first
/// scroll (reported on a phone 2026-09-26).
struct MaterialAdder: ViewModifier {
    @Binding var isPresented: Bool
    /// nil on the Materials screen; the category a See all screen shows.
    let group: MaterialGroup?
    let store: MaterialsStore
    let queue: MaterialUploadQueue

    @State private var next: AddMaterialSheet.Next?
    @State private var role: MaterialRole?
    @State private var showPhotos = false
    @State private var showFiles = false
    @State private var photos: [PhotosPickerItem] = []

    /// The Photos picker's own limit is far higher; this keeps one batch to
    /// what the queue card shows without scrolling far.
    private static let maxPick = 20

    func body(content: Content) -> some View {
        let accepts = MaterialAcceptance(group: group)
        content
            .sheet(isPresented: $isPresented, onDismiss: openNext) {
                AddMaterialSheet(store: store, group: group, accepts: accepts) { next = $0 }
            }
            // `.current`: the file as it is in the library. The default,
            // `.automatic`, lets Photos transcode (HEVC to H.264, HEIC to
            // JPEG) — a re-encode before the server, which stores video byte
            // for byte and turns HEIC into lossless PNG.
            .photosPicker(isPresented: $showPhotos, selection: $photos,
                          maxSelectionCount: Self.maxPick, selectionBehavior: .ordered,
                          matching: accepts.photos, preferredItemEncoding: .current)
            .fileImporter(isPresented: $showFiles, allowedContentTypes: accepts.fileTypes,
                          allowsMultipleSelection: true) { result in
                if let urls = try? result.get() { queue.add(files: urls, role: role) }
            }
            .onChange(of: photos) { _, items in
                guard !items.isEmpty else { return }
                queue.add(photos: items, role: role)
                photos = []
            }
    }

    private func openNext() {
        guard let next else { return }
        self.next = nil
        switch next {
        case .photos(let role): self.role = role; showPhotos = true
        case .files(let role): self.role = role; showFiles = true
        }
    }
}

/// What one entry point takes in: the Materials screen anything; a category
/// only what can be filed under it.
struct MaterialAcceptance {
    let photos: PHPickerFilter
    let fileTypes: [UTType]
    let allowsLink: Bool
    let allowsRoleChoice: Bool

    init(group: MaterialGroup?) {
        if group == nil {
            photos = .any(of: [.images, .videos]); fileTypes = [.image, .movie]
            allowsLink = true; allowsRoleChoice = true
        } else if group?.role == .driver {
            photos = .videos; fileTypes = [.movie]
            allowsLink = true; allowsRoleChoice = false
        } else {
            // An image role, or Unsorted (which only ever holds images).
            photos = .images; fileTypes = [.image]
            allowsLink = false; allowsRoleChoice = false
        }
    }
}

@MainActor
struct AddMaterialSheet: View {
    enum Next { case photos(MaterialRole?), files(MaterialRole?) }

    let store: MaterialsStore
    let group: MaterialGroup?
    let accepts: MaterialAcceptance
    /// What to open once this sheet has closed.
    let onNext: (Next) -> Void

    @Environment(\.dismiss) private var dismiss
    /// The last category chosen here, so a run of outfits is one tap each.
    /// "" is Unsorted.
    @AppStorage("materials.add.role") private var lastRole = ""
    @State private var showingLink = false
    @State private var link = ""
    @State private var failure: String?
    @FocusState private var linkFocused: Bool

    private var chosenRole: MaterialRole? {
        if let group { return group.role }
        return MaterialRole(rawValue: lastRole)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if accepts.allowsRoleChoice { rolePicker }
                    sources
                    if showingLink {
                        linkField.transition(.opacity.combined(with: .move(edge: .top)))
                    }
                    linkStatus
                }
                .padding(16)
            }
            .scrollBounceBehavior(.basedOnSize)
            .background(Theme.bg)
            .navigationTitle(group.map { "Add to \($0.title)" } ?? "Add material")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
        }
        .presentationDetents(accepts.allowsRoleChoice ? [.height(340), .medium] : [.height(220), .medium])
        .presentationDragIndicator(.visible)
    }

    // MARK: Category

    private var rolePicker: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Save photos as")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.secondary)
            HStack(spacing: 8) {
                ForEach(MaterialRole.options(for: .image), id: \.self) { role in
                    roleChip(role.title, value: role.rawValue)
                }
                roleChip("Unsorted", value: "")
            }
            Text("Videos always go to Motion drivers.")
                .font(.footnote)
                .foregroundStyle(Theme.tertiary)
        }
        .accessibilityElement(children: .contain)
    }

    private func roleChip(_ title: String, value: String) -> some View {
        let selected = lastRole == value
        return Button {
            withAnimation(.snappy(duration: 0.2)) { lastRole = value }
        } label: {
            Text(title)
                .font(.subheadline.weight(selected ? .semibold : .regular))
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .foregroundStyle(selected ? Theme.onAccent : Theme.label)
                .frame(maxWidth: .infinity, minHeight: 40)
                .background(selected ? Theme.accent : Theme.surface, in: .capsule)
                .contentShape(.capsule)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("add.role.\(value.isEmpty ? "unsorted" : value)")
    }

    // MARK: Sources

    private var sources: some View {
        HStack(spacing: 12) {
            SourceTile(title: "Photos", detail: "Pick several", systemImage: "photo.on.rectangle.angled") {
                close(then: .photos(chosenRole))
            }
            .accessibilityIdentifier("import.photos")

            if accepts.allowsLink {
                SourceTile(title: "TikTok link", detail: "Paste", systemImage: "link", selected: showingLink) {
                    withAnimation(.snappy) { showingLink.toggle() }
                    linkFocused = showingLink
                }
                .accessibilityIdentifier("import.tiktok")
            }

            SourceTile(title: "Files", detail: "Pick several", systemImage: "folder") {
                close(then: .files(chosenRole))
            }
            .accessibilityIdentifier("import.files")
        }
        .disabled(store.isImportingLink)
    }

    private func close(then next: Next) {
        onNext(next)
        dismiss()
    }

    // MARK: TikTok link

    private var linkField: some View {
        HStack(spacing: 8) {
            TextField("Paste a TikTok link", text: $link)
                .textContentType(.URL)
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.go)
                .focused($linkFocused)
                .onSubmit(importLink)
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
                .accessibilityIdentifier("import.tiktok.field")

            if TikTokLink.find(in: link) == nil {
                // The system paste control: no "Allow Paste" prompt.
                PasteButton(payloadType: String.self) { strings in
                    Task { @MainActor in
                        link = strings.first ?? ""
                        importLink()
                    }
                }
                .labelStyle(.iconOnly)
                .buttonBorderShape(.roundedRectangle(radius: Theme.Radius.medium))
                .tint(Theme.accent)
            } else {
                Button("Import", action: importLink)
                    .buttonStyle(.borderedProminent)
                    .buttonBorderShape(.roundedRectangle(radius: Theme.Radius.medium))
                    .foregroundStyle(Theme.onAccent)
                    .controlSize(.large)
                    .disabled(store.isImportingLink)
                    .accessibilityIdentifier("import.tiktok.go")
            }
        }
    }

    @ViewBuilder private var linkStatus: some View {
        if store.isImportingLink {
            Label {
                Text("Downloading from TikTok…")
            } icon: {
                ProgressView().controlSize(.small)
            }
            .font(.footnote).foregroundStyle(Theme.secondary)
        } else if let failure {
            Label(failure, systemImage: "exclamationmark.triangle.fill")
                .font(.footnote).foregroundStyle(Theme.warning)
        }
    }

    private func importLink() {
        guard !store.isImportingLink else { return }
        guard TikTokLink.find(in: link) != nil else {
            failure = link.isEmpty ? nil : "That isn't a TikTok link."
            return
        }
        failure = nil
        linkFocused = false
        let text = link
        Task {
            if await store.importLink(text) != nil {
                dismiss()
            } else {
                // Shown here, next to the field; left on the store it would
                // also fill the Materials banner behind the sheet.
                failure = store.errorMessage
                store.clearError()
            }
        }
    }
}

/// One import source: a large, labelled target rather than a toolbar glyph.
private struct SourceTile: View {
    let title: String
    let detail: String
    let systemImage: String
    var selected = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 4) {
                Image(systemName: systemImage)
                    .font(.title2)
                    .foregroundStyle(Theme.accent)
                    .frame(height: 30)
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.label)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(Theme.secondary)
            }
            .frame(maxWidth: .infinity, minHeight: 96)
            .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
            .overlay {
                if selected {
                    RoundedRectangle(cornerRadius: Theme.Radius.medium)
                        .strokeBorder(Theme.accent, lineWidth: 1.5)
                }
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
    }
}
