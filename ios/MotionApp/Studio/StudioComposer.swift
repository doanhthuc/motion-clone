import MotionKit
import SwiftUI

/// The pinned prompt bar: reference thumbnails, the prompt, ＋, the
/// "model · aspect · xN" pill and Send (spec "Project screen").
///
/// `peeking` is owned by `StudioSpaceView`, not this view: the long-press
/// backdrop dim has to cover the grid above the composer, and a dim drawn
/// from inside this view (pinned via `safeAreaInset`) can't reliably reach
/// past its own bounds. The composer only reads/writes the binding and still
/// draws the enlarged preview itself, since that floats just above its own
/// frame and isn't clipped by the inset.
struct StudioComposer: View {
    let studio: StudioStore
    @Binding var peeking: StudioRef?
    @State private var showSettings = false
    @State private var showSources = false
    @FocusState private var focused: Bool

    var body: some View {
        @Bindable var studio = studio
        VStack(alignment: .leading, spacing: 12) {
            if !studio.attachments.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(studio.attachments) { ref in
                            RefThumb(studio: studio, ref: ref, peeking: $peeking)
                        }
                    }
                }
            }
            TextField("What do you want to create?", text: $studio.prompt, axis: .vertical)
                .lineLimit(1...5)
                .focused($focused)
                .font(.title3)
                .accessibilityIdentifier("studio.prompt")
            HStack(spacing: 12) {
                Button { showSources = true } label: { Image(systemName: "plus").font(.title2) }
                    .accessibilityLabel("Add reference")
                    .accessibilityIdentifier("studio.add")
                Spacer()
                Button { showSettings = true } label: {
                    HStack(spacing: 6) {
                        Text(studio.selectedModel?.label ?? "Model").lineLimit(1)
                        Text(studio.aspect)
                        Text("x\(studio.count)")
                    }
                    .font(.footnote)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(Capsule().fill(Theme.surfaceRaised))
                }
                .accessibilityIdentifier("studio.settings")
                Button {
                    focused = false
                    Task { _ = await studio.send() }
                } label: {
                    if studio.isSending { ProgressView() } else {
                        Label(StudioFormat.usd(studio.estimateUSD), systemImage: "arrow.right")
                            .labelStyle(.titleAndIcon).font(.footnote.weight(.semibold))
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(!studio.canSend)
                .accessibilityIdentifier("studio.send")
            }
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 28).fill(Theme.surface).ignoresSafeArea(edges: .bottom))
        .overlay(alignment: .topLeading) {
            if let ref = peeking {
                RefPeek(studio: studio, ref: ref).offset(y: -236).transition(.scale(scale: 0.8).combined(with: .opacity))
            }
        }
        .sheet(isPresented: $showSettings) { StudioSettingsSheet(studio: studio).presentationDetents([.medium]) }
        .sheet(isPresented: $showSources) { StudioSourcePicker(studio: studio) }
    }
}

/// A reference thumbnail. The ⓧ appears only while this ref is being peeked
/// (long-press), as in Flow; a plain tap does nothing.
private struct RefThumb: View {
    let studio: StudioStore
    let ref: StudioRef
    @Binding var peeking: StudioRef?
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12).fill(Theme.surfaceRaised)
            if let image { Image(uiImage: image).resizable().scaledToFill() }
            if peeking == ref {
                Button { withAnimation(.snappy) { studio.detach(ref); peeking = nil } } label: {
                    Image(systemName: "xmark.circle").font(.title2).foregroundStyle(.white)
                        .shadow(radius: 2)
                }
                .accessibilityLabel("Remove reference")
                .accessibilityIdentifier("studio.ref.remove")
            }
        }
        .frame(width: 56, height: 56)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .onLongPressGesture(minimumDuration: 0.35) { withAnimation(.snappy) { peeking = ref } }
        .task { if let data = await studio.thumbnail(for: ref) { image = UIImage(data: data) } }
        .accessibilityIdentifier("studio.ref")
    }
}

private struct RefPeek: View {
    let studio: StudioStore
    let ref: StudioRef
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFit() } else { ProgressView() }
        }
        .frame(width: 160, height: 220)
        .background(RoundedRectangle(cornerRadius: 16).fill(Theme.surfaceRaised))
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .task { if let data = await studio.thumbnail(for: ref) { image = UIImage(data: data) } }
    }
}
