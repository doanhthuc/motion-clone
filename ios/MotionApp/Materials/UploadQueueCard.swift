import MotionKit
import SwiftUI

/// The batch in flight: one line of what is happening, one bar for the whole
/// batch, and a strip with every item's own state — so "which one failed" is
/// answered by looking, not by reading file names.
struct UploadQueueCard: View {
    let queue: MaterialUploadQueue

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if queue.isRunning {
                ProgressView(value: queue.fraction)
                    .tint(Theme.accent)
                    .animation(.linear(duration: 0.2), value: queue.fraction)
            }
            strip
            footer
        }
        .heroSurface()
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("materials.upload")
    }

    private var destination: String {
        queue.role.map { MaterialGroup.title(for: $0) } ?? "Materials"
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 10) {
            statusIcon
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(destination).font(.footnote).foregroundStyle(Theme.secondary)
            }
            Spacer(minLength: 0)
            if queue.isRunning {
                Text(queue.fraction, format: .percent.precision(.fractionLength(0)))
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(Theme.secondary)
                    .contentTransition(.numericText())
            } else {
                Button {
                    withAnimation(.snappy) { queue.clear() }
                } label: {
                    Image(systemName: "xmark")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(Theme.secondary)
                        .frame(width: 30, height: 30)
                        .background(Theme.surfaceRaised, in: .circle)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Dismiss")
            }
        }
    }

    private var title: String {
        let done = queue.doneCount, failed = queue.failedCount, total = queue.total
        if queue.isRunning {
            let position = min(done + failed + 1, total)
            return total == 1 ? "Uploading…" : "Uploading \(position) of \(total)"
        }
        if failed == 0 { return done == 1 ? "Added" : "\(done) added" }
        if done == 0 { return failed == 1 ? "Upload failed" : "\(failed) uploads failed" }
        return "\(done) added · \(failed) failed"
    }

    @ViewBuilder private var statusIcon: some View {
        Group {
            if queue.isRunning {
                ProgressView().controlSize(.small)
            } else if queue.failedCount == 0 {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.accent)
            } else {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.warning)
            }
        }
        .font(.title3)
        .frame(width: 28, height: 28)
    }

    private var strip: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal) {
                HStack(spacing: 8) {
                    ForEach(queue.items) { item in
                        UploadThumb(item: item,
                                    progress: item.state == .uploading ? queue.currentFraction : nil)
                            .id(item.id)
                    }
                }
                .padding(.horizontal, 16)
            }
            .scrollIndicators(.hidden)
            .padding(.horizontal, -16)
            .onChange(of: queue.current?.id) { _, id in
                guard let id else { return }
                withAnimation(.snappy) { proxy.scrollTo(id, anchor: .center) }
            }
        }
    }

    @ViewBuilder private var footer: some View {
        if let current = queue.current {
            HStack(spacing: 6) {
                Text(current.name).lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 8)
                if let progress = queue.currentProgress, progress.phase == .transferring {
                    Text("\(bytes(progress.bytesSent)) of \(bytes(progress.totalBytes))")
                        .monospacedDigit()
                } else if queue.currentProgress?.phase == .processing {
                    Text("Processing…")
                } else {
                    Text("Preparing…")
                }
            }
            .font(.footnote)
            .foregroundStyle(Theme.secondary)
        } else if let failure = queue.items.lazy.compactMap(\.failure).first {
            Text(failure)
                .font(.footnote)
                .foregroundStyle(Theme.warning)
                .lineLimit(2)
        }
    }

    private func bytes(_ count: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: count, countStyle: .file)
    }
}

private struct UploadThumb: View {
    let item: MaterialUploadQueue.Item
    /// 0…1 for the item uploading now; nil for the others.
    let progress: Double?

    var body: some View {
        ZStack {
            if let thumbnail = item.thumbnail {
                Image(uiImage: thumbnail).resizable().scaledToFill()
            } else {
                Theme.surfaceRaised
                Image(systemName: "photo").font(.footnote).foregroundStyle(Theme.tertiary)
            }
            overlay
        }
        .frame(width: 48, height: 60)
        .clipShape(.rect(cornerRadius: Theme.Radius.small))
        .opacity(item.state == .waiting ? 0.45 : 1)
        .animation(.snappy, value: item.state)
    }

    @ViewBuilder private var overlay: some View {
        switch item.state {
        case .waiting:
            EmptyView()
        case .uploading:
            ZStack {
                Color.black.opacity(0.35)
                Circle()
                    .stroke(.white.opacity(0.3), lineWidth: 3)
                    .frame(width: 24, height: 24)
                Circle()
                    .trim(from: 0, to: max(0.04, progress ?? 0))
                    .stroke(.white, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .frame(width: 24, height: 24)
                    .animation(.linear(duration: 0.2), value: progress)
            }
        case .done:
            badge("checkmark", Theme.accent, foreground: Theme.onAccent)
        case .failed:
            ZStack {
                Color.black.opacity(0.35)
                badge("exclamationmark", Theme.danger, foreground: .white)
            }
        }
    }

    private func badge(_ symbol: String, _ fill: Color, foreground: Color) -> some View {
        Image(systemName: symbol)
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(foreground)
            .frame(width: 18, height: 18)
            .background(fill, in: .circle)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
            .padding(4)
    }
}

private extension MaterialUploadQueue.Item {
    var failure: String? {
        if case .failed(let message) = state { message } else { nil }
    }
}
