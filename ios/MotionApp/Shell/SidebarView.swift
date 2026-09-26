import MotionKit
import SwiftUI

/// The slide-out panel: the two spaces, Studio's projects, Settings. Modelled
/// on the Claude app's space switcher (spec 2026-09-26 "Shell and sidebar").
struct SidebarView: View {
    @Environment(AppModel.self) private var model
    let studio: StudioStore
    @State private var renaming: StudioProjectSummary?
    @State private var newTitle = ""
    @State private var showSettings = false

    var body: some View {
        List {
            Section {
                spaceRow(.motion, title: "Motion", icon: "waveform.path.ecg")
                spaceRow(.studio, title: "Image Studio", icon: "sparkles")
            }
            Section("Projects") {
                Button { Task { await model.openStudio(projectID: nil) } } label: {
                    Label("New project", systemImage: "plus")
                }
                ForEach(studio.projects) { p in
                    Button { Task { await model.openStudio(projectID: p.id) } } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(StudioFormat.title(p.title, createdAt: p.createdAt)).lineLimit(1)
                            Text("\(p.imageCount) images · \(StudioFormat.usd(p.spentUsd))")
                                .font(.caption).foregroundStyle(Theme.secondary)
                        }
                    }
                    .listRowBackground(studio.project?.id == p.id && model.selectedSpace == .studio
                                       ? Theme.surfaceRaised : Theme.surface)
                    .contextMenu {
                        Button("Rename", systemImage: "pencil") { newTitle = p.title; renaming = p }
                        Button("Delete", systemImage: "trash", role: .destructive) {
                            Task { await studio.delete(p.id) }
                        }
                    }
                }
            }
            Section {
                Button { showSettings = true } label: { Label("Settings", systemImage: "gearshape") }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
        .task { await studio.loadProjects() }
        .alert("Rename project", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Title", text: $newTitle)
            Button("Save") { if let p = renaming { Task { await studio.rename(p.id, to: newTitle) } } }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(isPresented: $showSettings) { NavigationStack { SettingsView(firstRun: false) } }
    }

    private func spaceRow(_ space: AppSpace, title: String, icon: String) -> some View {
        Button {
            model.selectedSpace = space
            model.isSidebarOpen = false
        } label: {
            Label(title, systemImage: icon).fontWeight(model.selectedSpace == space ? .semibold : .regular)
        }
        .accessibilityIdentifier("sidebar.\(space.rawValue)")
    }
}

enum StudioFormat {
    static func title(_ title: String, createdAt: Double) -> String {
        title.isEmpty ? Date(timeIntervalSince1970: createdAt).formatted(date: .abbreviated, time: .shortened) : title
    }

    static func usd(_ value: Double) -> String {
        value.formatted(.currency(code: "USD").precision(.fractionLength(value < 1 ? 3 : 2)))
    }
}
