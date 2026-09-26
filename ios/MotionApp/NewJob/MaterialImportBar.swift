import MotionKit
import PhotosUI
import SwiftUI

/// The top of every material picker: bring new material in without leaving
/// the job. Photos for any role; a pasted TikTok link too when the role takes
/// a video, since that is where drivers come from in practice (the bot has
/// taken links since 2026-09-04 — `scripts/tgbot/tiktok.py`).
@MainActor
struct MaterialImportBar: View {
    let materials: MaterialsStore
    /// Called with each material this bar produced, once it is in the list.
    let onImported: (MotionKit.Material) -> Void
    private let photos: PHPickerFilter
    private let allowsLink: Bool
    /// Filed under this role on arrival, so the Materials tab groups it.
    private let role: MaterialRole?

    /// For one pipeline role: Photos filtered to its kind, a link for video.
    init(kind: PipelineRoleKind, role: MaterialRole? = nil, materials: MaterialsStore,
         onImported: @escaping (MotionKit.Material) -> Void) {
        self.materials = materials
        self.onImported = onImported
        photos = kind == .video ? .videos : .images
        allowsLink = kind == .video
        self.role = role
    }

    @State private var photoItem: PhotosPickerItem?
    @State private var showingLink = false
    @State private var link = ""
    @State private var failure: String?
    @FocusState private var linkFocused: Bool

    private var busy: Bool { materials.isUploading || materials.isImportingLink }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                // `.current`: the file as it is in the library. The default,
                // `.automatic`, lets Photos transcode to a "compatible" format
                // (HEVC video to H.264, HEIC to JPEG) — a re-encode before the
                // server, which itself stores video byte for byte and turns
                // HEIC into lossless PNG.
                PhotosPicker(selection: $photoItem, matching: photos,
                             preferredItemEncoding: .current) {
                    SourceTile(title: "Photos", systemImage: "photo.on.rectangle.angled")
                }
                .accessibilityIdentifier("import.photos")

                if allowsLink {
                    Button {
                        withAnimation(.snappy) { showingLink.toggle() }
                        linkFocused = showingLink
                    } label: {
                        SourceTile(title: "TikTok link", systemImage: "link", selected: showingLink)
                    }
                    .accessibilityIdentifier("import.tiktok")
                }
            }
            .buttonStyle(.plain)
            .disabled(busy)

            if showingLink && allowsLink {
                linkField
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
            status
        }
        .onChange(of: photoItem) { _, item in importPhoto(item) }
    }

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
                // The system paste control: no "Allow Paste" prompt, and a
                // copied share caption is trimmed to its link below.
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
                    .disabled(busy)
                    .accessibilityIdentifier("import.tiktok.go")
            }
        }
    }

    @ViewBuilder private var status: some View {
        if let progress = materials.uploadProgress, materials.isUploading {
            VStack(alignment: .leading, spacing: 6) {
                Text("Uploading \(progress.fileName)")
                    .font(.footnote).foregroundStyle(Theme.secondary)
                    .lineLimit(1).truncationMode(.middle)
                ProgressView(value: Double(progress.bytesSent), total: Double(max(progress.totalBytes, 1)))
                    .tint(Theme.accent)
            }
        } else if materials.isImportingLink {
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

    private func importPhoto(_ item: PhotosPickerItem?) {
        guard let item else { return }
        failure = nil
        Task {
            defer { photoItem = nil }
            do {
                if let material = try await MediaImport.upload(item, to: materials) {
                    await arrived(material)
                } else {
                    takeStoreFailure()
                }
            } catch {
                failure = error.localizedDescription
            }
        }
    }

    private func importLink() {
        guard !busy else { return }
        guard TikTokLink.find(in: link) != nil else {
            failure = link.isEmpty ? nil : "That isn't a TikTok link."
            return
        }
        failure = nil
        linkFocused = false
        let text = link
        Task {
            if let material = await materials.importLink(text) {
                link = ""
                withAnimation(.snappy) { showingLink = false }
                await arrived(material)
            } else {
                takeStoreFailure()
            }
        }
    }

    /// Moves the store's error here, next to the control that caused it. Left
    /// on the store it would also show in the picker's list-refresh banner,
    /// whose Retry re-reads the list and would not retry the import.
    private func arrived(_ material: MotionKit.Material) async {
        if let role, MaterialRole.options(for: material.kind).contains(role) {
            await materials.setRole(role, for: material)
        }
        onImported(material)
    }

    /// Kept on the store when an upload can still resume: the Materials tab's
    /// banner is the only place that offers its Retry / Discard.
    private func takeStoreFailure() {
        failure = materials.errorMessage
        if !materials.hasPendingUpload { materials.clearError() }
    }
}

/// One import source: a large, labelled target rather than a toolbar glyph,
/// so "where do I add a new one" is answered by looking.
private struct SourceTile: View {
    let title: String
    let systemImage: String
    var selected = false

    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: systemImage)
                .font(.title3)
                .foregroundStyle(Theme.accent)
            Text(title)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(Theme.label)
        }
        .frame(maxWidth: .infinity, minHeight: 72)
        .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
        .overlay {
            if selected {
                RoundedRectangle(cornerRadius: Theme.Radius.medium)
                    .strokeBorder(Theme.accent, lineWidth: 1.5)
            }
        }
        .contentShape(.rect)
    }
}
