<template>
  <!-- #region ALD 22/05/2026 - Apple HIG workflow editor
       Frosted glass sidebars, System Blue accent, SF Pro typography. -->
  <div
    class="apl-editor"
    @keydown.meta.s.prevent="isOwned && onSave()"
    @keydown.ctrl.s.prevent="isOwned && onSave()"
    @keydown.meta.a="selectAllNodes"
    @keydown.ctrl.a="selectAllNodes"
    @keydown.esc="clearSelection"
    tabindex="-1"
  >
    <!-- LEFT — node palette (mobile: off-canvas drawer, toggle bằng nút ☰ trên topbar) -->
    <aside :class="['apl-sidebar', 'apl-sidebar-left', paletteOpen && 'is-open']">
      <div class="apl-sidebar-header">
        <NuxtLink to="/" class="apl-back">
          <i class="bi bi-chevron-left" />
          <span>Workflows</span>
        </NuxtLink>
        <button type="button" class="apl-palette-close" title="Đóng danh sách node" @click="paletteOpen = false">
          <i class="bi bi-x-lg" />
        </button>
      </div>
      <div class="apl-search-wrap">
        <i class="bi bi-search apl-search-icon" />
        <input v-model="paletteSearch" type="text" placeholder="Tìm node" class="apl-search-input" />
      </div>
      <div class="apl-palette">
        <div v-for="cat in filteredCategories" :key="cat.id" class="apl-cat">
          <p class="apl-cat-label">{{ cat.label }}</p>
          <div class="apl-cat-list">
            <div
              v-for="t in cat.nodes"
              :key="t.id"
              :draggable="!isViewingHistory"
              :class="['apl-palette-item', isViewingHistory && 'is-disabled']"
              @dragstart="onDragStart($event, t.id)"
              @click="isMobile && onPaletteItemTap(t.id)"
            >
              <span class="apl-palette-icon" :style="{ background: t.soft, color: t.color }">
                <i :class="['bi', t.icon]" />
              </span>
              <div class="apl-palette-text">
                <span class="apl-palette-label">{{ t.label }}</span>
                <span class="apl-palette-hint">{{ t.hint }}</span>
              </div>
            </div>
          </div>
        </div>
        <p v-if="filteredCategories.length === 0" class="apl-empty">Không có node khớp</p>
      </div>
      <div class="apl-sidebar-footer">
        <template v-if="isOwned">
          <kbd class="apl-kbd">⌘S</kbd> Lưu
          <span class="mx-1.5 text-gray-300">·</span>
        </template>
        <kbd class="apl-kbd">⌘A</kbd> Chọn tất
        <span class="mx-1.5 text-gray-300">·</span>
        <kbd class="apl-kbd">⌫</kbd> Xoá
      </div>
    </aside>

    <!-- Mobile: nền mờ đóng drawer palette khi chạm ra ngoài -->
    <div v-if="paletteOpen" class="apl-palette-backdrop" @click="paletteOpen = false" />

    <!-- CENTER — canvas -->
    <main class="apl-canvas-area">
      <!-- ALD 24/05/2026 - Topbar redesigned Apple Island: title left, action capsule right
           grouping secondary (Lịch sử / Runs) vs primary (Chạy workflow + Lưu). -->
      <div class="apl-topbar">
        <div class="min-w-0 flex items-center gap-3">
          <!-- Mobile: nút ☰ mở drawer node-list (ẩn ở desktop qua CSS) -->
          <button type="button" class="apl-palette-toggle" title="Danh sách node" @click="paletteOpen = true">
            <i class="bi bi-list" />
          </button>
          <div class="min-w-0">
            <h1 class="apl-title">{{ workflow?.name || '...' }}</h1>
            <p class="apl-subtitle">/{{ workflow?.slug }}</p>
          </div>
        </div>

        <div class="apl-actions">
          <!-- Secondary group: Lịch sử + Runs drawer -->
          <div class="apl-action-group">
            <NuxtLink :to="`/workflows/${route.params.id}/runs`" class="apl-icon-btn" title="Lịch sử run">
              <i class="bi bi-clock-history" />
            </NuxtLink>
            <button
              v-if="isOwned && !isViewingHistory"
              type="button"
              class="apl-icon-btn"
              title="Sửa thông tin workflow"
              @click="openWorkflowInfo"
            >
              <i class="bi bi-pencil-square" />
            </button>
            <button
              v-if="testHistory.length > 0 || testRunning"
              type="button"
              :class="['apl-icon-btn', drawerVisible && 'is-active']"
              :title="drawerVisible ? 'Ẩn runs' : 'Hiện runs'"
              @click="drawerVisible = !drawerVisible"
            >
              <i class="bi bi-list-ul" />
              <span v-if="runCounts.running > 0" class="apl-icon-btn-badge apl-badge-running">
                <i class="bi bi-arrow-repeat animate-spin" />
              </span>
              <span v-else-if="testHistory.length > 0" class="apl-icon-btn-badge">{{ testHistory.length }}</span>
            </button>
            <!-- ALD 30/06/2026 - Generator "Dựng từ kịch bản": nhập kịch bản → AI (Qwen) phân tích + tự gọi
                 tool dựng các node sẵn có → workflow hoàn chỉnh trên canvas. (Thay generator Multi-outfit cũ.) -->
            <button
              v-if="isOwned && !isViewingHistory"
              type="button"
              class="apl-icon-btn"
              title="Dựng workflow từ kịch bản (AI phân tích → tự dựng nodes)"
              @click="moGenOpen = true; moGenCollapsed = false"
            >
              <i class="bi bi-magic" />
            </button>
            <!-- ALD 08/07/2026 - Dựng SẴN mẫu "Thử đồ 2 bộ → Đè lộ" để dễ test (tryon×2 → motion×2 5s → đè lộ). -->
            <button
              v-if="isOwned && !isViewingHistory"
              type="button"
              class="apl-icon-btn"
              title="Dựng mẫu sẵn: Thử đồ 2 bộ → Motion → Đè lộ (dễ test — chỉ cần upload 4 ảnh/video rồi Chạy)"
              @click="insertTryonRevealTemplate"
            >
              <i class="bi bi-layers-half" />
            </button>
            <button
              v-if="isOwned && !isViewingHistory"
              type="button"
              class="apl-icon-btn apl-icon-btn-danger"
              title="Xoá workflow"
              @click="deleteWorkflow"
            >
              <i class="bi bi-trash" />
            </button>
          </div>

          <!-- Primary: Save + Run capsules (Apple Island style) -->
          <!-- ALD 27/05/2026 - Ẩn nút Lưu nếu workflow không phải của user (public viewer) -->
          <!-- ALD 28/05/2026 - Khi viewing history: ẩn Save + Run, show "New Session" button.
               Lịch sử = READ-ONLY view (preview kết quả run cũ). Muốn run mới = New Session
               để clear canvas → user upload lại + edit → Chạy workflow xuất hiện trở lại. -->
          <button
            v-if="isOwned && !isViewingHistory"
            type="button"
            :disabled="!dirty || saving"
            :class="['apl-cta apl-cta-secondary', (!dirty || saving) && 'is-disabled']"
            :title="dirty ? 'Lưu thay đổi (⌘S)' : 'Không có thay đổi'"
            @click="onSave"
          >
            <i :class="['bi', saving ? 'bi-arrow-repeat animate-spin' : 'bi-cloud-arrow-up']" />
            <span>Lưu</span>
          </button>
          <!-- ALD 12/06/2026 - HAI nút TÁCH BIỆT (chốt theo feedback):
               (1) "Phiên mới" = về trang soạn thảo trống để nhập prompt mới, TUYỆT ĐỐI KHÔNG tự chạy.
               (2) "Chạy workflow" = chạy; đang có run thì hiện "Đang xử lý (N)" nhưng vẫn bấm được (song song). -->
          <button
            type="button"
            class="apl-cta apl-cta-secondary"
            title="Mở phiên soạn thảo mới (nhập prompt mới) — KHÔNG chạy gì"
            @click="newBlankSession"
          >
            <i class="bi bi-plus-lg" />
            <span>Phiên mới</span>
          </button>
          <!-- ALD 12/06/2026 - nút LUÔN là hành động "Chạy workflow" (KHÔNG hiện "Đang xử lý" của job cũ —
               processing là của TAB run riêng, không phải của nút này). Đang có run khác vẫn bấm chạy thêm
               (song song); chỉ chặn khi đụng trần. -->
          <button
            type="button"
            :disabled="runningCount >= MAX_CONCURRENT_RUNS"
            class="apl-cta apl-cta-primary"
            :title="runningCount >= MAX_CONCURRENT_RUNS
              ? `Tối đa ${MAX_CONCURRENT_RUNS} run song song — đợi bớt hoặc cancel`
              : 'Chạy workflow (tạo run mới)'"
            @click="openTestRun"
          >
            <i class="bi bi-play-fill" />
            <span>Chạy workflow</span>
          </button>
        </div>
      </div>

      <!-- ALD 12/06/2026 - MULTI-RUN TAB kiểu VS Code: tab "Soạn thảo" (trang canvas, luôn có khi đã có run)
           + 1 tab/run. Nút "Phiên mới" trên toolbar = về & active tab Soạn thảo này (để THẤY rõ trang canvas).
           Run đang chạy = spinner; xong giữ tab tới khi ✕. -->
      <div v-if="runTabs.length" class="apl-runtabs">
        <button
          type="button"
          :class="['apl-runtab', !selectedRunId && 'is-active']"
          title="Trang soạn thảo (canvas) — chỉnh prompt/node rồi bấm Chạy workflow"
          @click="focusEditTab()"
        >
          <i class="bi bi-pencil-square" />
          <span>New workflow</span>
        </button>
        <button
          v-for="t in runTabs"
          :key="t.id"
          type="button"
          :class="['apl-runtab', selectedRunId === t.id && 'is-active', `is-${effectiveRunStatus(t)}`]"
          :title="effectiveRunStatus(t) === 'running' ? 'Run đang chạy — click để theo dõi' : `Run ${effectiveRunStatus(t)}`"
          @click="focusRunTab(t.id)"
        >
          <i :class="['bi', effectiveRunStatus(t) === 'running' ? 'bi-arrow-repeat apl-runtab-spin'
            : effectiveRunStatus(t) === 'success' ? 'bi-check-circle-fill' : 'bi-x-circle-fill']" />
          <span>Run {{ tabTime(t) }}</span>
          <i
            v-if="effectiveRunStatus(t) !== 'running'"
            class="bi bi-x apl-runtab-x"
            title="Đóng tab (run vẫn nằm trong lịch sử)"
            @click.stop="closeRunTab(t.id)"
          />
        </button>
      </div>
      <div class="apl-canvas" @drop="onDrop" @dragover.prevent>
        <ClientOnly>
          <VueFlow
            v-model:nodes="nodes"
            v-model:edges="edges"
            :node-types="customNodeTypes"
            :default-edge-options="{ type: 'step', animated: false, style: { strokeWidth: 2, stroke: '#94a3b8' }, pathOptions: { borderRadius: 20, offset: 24 } }"
            fit-view-on-init
            :min-zoom="0.3"
            :max-zoom="2"
            :snap-to-grid="true"
            :snap-grid="[16, 16]"
            :nodes-draggable="!isViewingHistory"
            :nodes-connectable="!isViewingHistory"
            :edges-updatable="!isViewingHistory"
            :delete-key-code="isViewingHistory ? null : ['Delete', 'Backspace']"
            @node-click="onNodeClick"
            @pane-click="selectedNodeId = null"
            @connect="onConnect"
            @nodes-change="onNodesChange"
          >
            <Background pattern-color="#232329" :gap="24" :size="1" />
            <Controls position="bottom-left" />
          </VueFlow>
          <div v-if="nodes.length === 0" class="apl-empty-state">
            <span class="apl-empty-icon">
              <i class="bi bi-stars" />
            </span>
            <p class="apl-empty-title">Canvas trống</p>
            <p class="apl-empty-hint">Kéo node từ panel trái để bắt đầu</p>
          </div>
        </ClientOnly>
        <ClientOnly>
          <AdminComfyLogsDrawer />
        </ClientOnly>
      </div>

      <!-- Test history drawer — 2 cột: list bên trái, detail bên phải -->
      <Transition enter-active-class="transition duration-250 ease-out" leave-active-class="transition duration-200 ease-in" enter-from-class="translate-y-full opacity-0" leave-to-class="translate-y-full opacity-0">
        <div v-if="drawerVisible && (testHistory.length > 0 || testRunning)" class="apl-run-drawer" :style="{ height: drawerHeight + 'px' }">
          <!-- ALD 27/05/2026 - Drag-to-resize: kéo handle top edge để tăng/giảm chiều cao.
               Persist localStorage để giữ qua reload. Pattern mượn từ pebsteel-ai. -->
          <div class="apl-drawer-resize" @mousedown.prevent="startDrawerResize" title="Kéo để điều chỉnh chiều cao">
            <div class="apl-drawer-resize-line" />
          </div>
          <!-- Header với filter chips + actions -->
          <div class="apl-drawer-header">
            <span class="apl-drawer-title">
              <i class="bi bi-clock-history mr-1" />
              Lịch sử
            </span>
            <!-- Filter chips -->
            <div class="apl-filter-chips ml-3">
              <button type="button" :class="['apl-chip', runFilter === 'all' && 'is-active']" @click="runFilter = 'all'">
                All <span class="apl-chip-count">{{ runCounts.all }}</span>
              </button>
              <button type="button" :class="['apl-chip', 'apl-chip-success', runFilter === 'success' && 'is-active']" @click="runFilter = 'success'">
                OK <span class="apl-chip-count">{{ runCounts.success }}</span>
              </button>
              <button type="button" :class="['apl-chip', 'apl-chip-error', runFilter === 'error' && 'is-active']" @click="runFilter = 'error'">
                Fail <span class="apl-chip-count">{{ runCounts.error }}</span>
              </button>
            </div>
            <div class="ml-auto flex items-center gap-1">
              <button v-if="testHistory.length" type="button" class="apl-text-btn" @click="clearTestHistory" title="Xoá toàn bộ test history">
                <i class="bi bi-trash mr-1" />Clear
              </button>
              <button type="button" class="apl-close" title="Ẩn (giữ history)" @click="drawerVisible = false">
                <i class="bi bi-chevron-down" />
              </button>
            </div>
          </div>

          <div class="apl-drawer-body">
            <!-- LEFT — Run list -->
            <div class="apl-history-list">
              <!-- History items: row hover lộ nút xoá riêng từng entry. -->
              <div
                v-for="r in filteredHistory"
                :key="r.id"
                :class="['apl-history-item', selectedRunId === r.id && 'is-selected', effectiveRunStatus(r) === 'running' && 'is-running']"
              >
                <button type="button" class="apl-history-main" @click="focusRunTab(r.id)">
                  <span :class="[
                    'apl-status-pill',
                    effectiveRunStatus(r) === 'success' ? 'apl-pill-success'
                    : effectiveRunStatus(r) === 'running' ? 'apl-pill-running'
                    : 'apl-pill-error'
                  ]">
                    <i :class="[
                      'bi',
                      effectiveRunStatus(r) === 'success' ? 'bi-check-lg'
                      : effectiveRunStatus(r) === 'running' ? 'bi-arrow-repeat animate-spin'
                      : 'bi-x-lg'
                    ]" />
                    {{ effectiveRunStatus(r) === 'success' ? 'OK' : effectiveRunStatus(r) === 'running' ? 'Running' : 'Fail' }}
                  </span>
                  <div class="min-w-0 flex-1">
                    <div class="apl-history-title">
                      {{ fmtTime(r.ts) }} ·
                      <template v-if="effectiveRunStatus(r) === 'running'">{{ fmtMs(Date.now() - r.ts) }}</template>
                      <template v-else>{{ fmtMs(r.durationMs) }}</template>
                    </div>
                    <div class="apl-history-meta">
                      {{ r.snapshot?.nodeCount || '?' }} nodes
                      <template v-if="effectiveRunStatus(r) === 'running'">
                        · <span class="apl-history-running-text">{{ (r.events?.[r.events.length-1]?.msg) || 'chờ kết quả…' }}</span>
                      </template>
                      <template v-else-if="effectiveRunStatus(r) === 'error' && r.error"> · <span class="apl-history-err">{{ errorPreview(r.error) }}</span></template>
                      <template v-else-if="r.output?.text"> · <span class="apl-history-out">{{ outputPreview(r.output.text) }}</span></template>
                    </div>
                  </div>
                </button>
                <button
                  v-if="effectiveRunStatus(r) !== 'running'"
                  type="button"
                  class="apl-history-delete"
                  title="Xoá run này"
                  @click.stop="deleteSingleRun(r)"
                >
                  <i class="bi bi-x" />
                </button>
              </div>

              <!-- Empty state khi filter ra 0 result -->
              <div v-if="filteredHistory.length === 0 && !testRunning" class="apl-history-empty-list">
                <i class="bi bi-funnel text-gray-300 text-xl" />
                <p class="text-[11px] text-gray-400 mt-1">Không có run nào khớp filter</p>
                <button type="button" class="apl-text-btn mt-1" @click="runFilter = 'all'">Xem tất cả</button>
              </div>
            </div>

            <!-- RIGHT — Detail panel -->
            <div v-if="selectedTestRun" class="apl-history-detail">
              <!-- Detail header: status + timestamps + retry -->
              <div class="apl-detail-header">
                <span :class="[
                  'apl-status-pill',
                  selectedRunStatus === 'success' ? 'apl-pill-success'
                  : selectedRunStatus === 'running' ? 'apl-pill-running'
                  : 'apl-pill-error'
                ]">
                  <i :class="[
                    'bi',
                    selectedRunStatus === 'success' ? 'bi-check-lg'
                    : selectedRunStatus === 'running' ? 'bi-arrow-repeat animate-spin'
                    : 'bi-x-lg'
                  ]" />
                  {{ selectedRunStatus === 'success' ? 'Success' : selectedRunStatus === 'running' ? 'Running' : 'Failed' }}
                </span>
                <span class="apl-detail-meta">
                  <i class="bi bi-clock mr-1" />{{ new Date(selectedTestRun.ts).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'medium' }) }}
                </span>
                <span class="apl-detail-meta">
                  <i class="bi bi-stopwatch mr-1" />
                  <template v-if="selectedRunStatus === 'running'">{{ fmtMs(Date.now() - selectedTestRun.ts) }} elapsed</template>
                  <template v-else>{{ fmtMs(selectedTestRun.durationMs) }}</template>
                </span>
                <span class="apl-detail-meta">
                  <i class="bi bi-diagram-3 mr-1" />{{ selectedTestRun.snapshot?.nodeCount || '?' }} nodes
                </span>
                <button v-if="selectedRunStatus === 'running'" type="button" class="apl-detail-action apl-detail-cancel ml-auto" @click="cancelRun(selectedTestRun)" title="Stop poll + mark cancel">
                  <i class="bi bi-stop-circle mr-1" />Cancel
                </button>
                <template v-else>
                  <!-- ALD 17/06/2026 - Run lỗi: "Tiếp tục" = dùng lại bước đã render xong, chỉ render node lỗi + sau (cache theo nội dung). -->
                  <button v-if="selectedRunStatus === 'error'" type="button" class="apl-detail-action apl-detail-resume ml-auto" :disabled="hasActiveRun" @click="resumeFromHistory(selectedTestRun)" title="Tiếp tục từ chỗ lỗi: dùng lại bước đã xong, chỉ render node lỗi + phía sau">
                    <i class="bi bi-skip-forward-fill mr-1" />Tiếp tục
                  </button>
                  <button type="button" class="apl-detail-action" :class="{ 'ml-auto': selectedRunStatus !== 'error' }" :disabled="hasActiveRun" @click="rerunFromHistory(selectedTestRun)" title="Chạy lại từ đầu với input này (render mới toàn bộ)">
                    <i class="bi bi-arrow-clockwise mr-1" />Re-run
                  </button>
                </template>
              </div>

              <!-- Detail body -->
              <div class="apl-detail-body">
                <!-- ALD 24/05/2026 - Job state panel: 4 layouts riêng (running / cancelled / error / done).
                     Tách hẳn để UX rõ ràng, không reuse layout running cho cancelled. -->
                <!-- RUNNING — amber, progress bar + cancel button -->
                <section v-if="pendingJobInfo?.state === 'running'" class="px-4 py-2 apl-detail-section apl-section-pending">
                  <div class="apl-section-head apl-pending-head">
                    <i class="bi bi-arrow-repeat animate-spin text-amber-500" />
                    <span class="apl-section-title">Job đang chạy</span>
                    <span class="apl-pending-kind">{{ pendingJobInfo.kind }}</span>
                    <button type="button" class="apl-pending-cancel" title="Huỷ job" @click="onCancelMotionJob(pendingJobInfo.job_id)">
                      <i class="bi bi-x-circle" /> Huỷ
                    </button>
                  </div>
                  <div class="apl-pending-body">
                    <div class="apl-pending-step">{{ pendingJobInfo.current_step || 'processing…' }}</div>
                    <div class="apl-pending-bar">
                      <div class="apl-pending-fill" :style="{ width: `${Math.round(pendingJobInfo.progress * 100)}%` }" />
                    </div>
                    <div class="apl-pending-meta">
                      <span class="font-mono">{{ pendingJobInfo.job_id?.slice(0, 8) }}</span>
                      <span>·</span>
                      <span class="apl-pending-pct">{{ Math.round(pendingJobInfo.progress * 100) }}%</span>
                      <span v-if="pendingJobInfo.eta">· ETA {{ pendingJobInfo.eta }}</span>
                    </div>
                  </div>
                </section>

                <!-- CANCELLED — gray, simple message + re-run hint -->
                <section v-else-if="pendingJobInfo?.state === 'cancelled'" class="px-4 py-2 apl-detail-section apl-section-cancelled">
                  <div class="apl-section-head">
                    <i class="bi bi-slash-circle text-gray-500" />
                    <span class="apl-section-title text-gray-700">Job đã huỷ</span>
                    <span class="apl-pending-kind">{{ pendingJobInfo.kind }}</span>
                  </div>
                  <div class="apl-pending-body">
                    <div class="apl-pending-step text-gray-500 italic">Worker đã dừng, GPU đã free. Có thể chạy lại workflow nếu cần.</div>
                    <div class="apl-pending-meta">
                      <span class="font-mono text-gray-500">{{ pendingJobInfo.job_id?.slice(0, 8) }}</span>
                    </div>
                  </div>
                </section>

                <!-- ERROR — red, error details + retry hint -->
                <section v-else-if="pendingJobInfo?.state === 'error'" class="px-4 py-2 apl-detail-section apl-section-job-error">
                  <div class="apl-section-head">
                    <i class="bi bi-exclamation-octagon-fill text-rose-500" />
                    <span class="apl-section-title text-rose-700">Job lỗi</span>
                    <span class="apl-pending-kind">{{ pendingJobInfo.kind }}</span>
                  </div>
                  <div class="apl-pending-body">
                    <pre v-if="pendingJobInfo.error" class="text-[11px] text-rose-700 bg-rose-50/60 border border-rose-200 rounded-md px-2 py-1.5 whitespace-pre-wrap font-mono">{{ pendingJobInfo.error.split('\n')[0].slice(0, 200) }}</pre>
                    <div class="apl-pending-meta">
                      <span class="font-mono text-rose-700">{{ pendingJobInfo.job_id?.slice(0, 8) }}</span>
                    </div>
                  </div>
                </section>

                <!-- DONE — emerald, inline video preview + link copy/download -->
                <section v-else-if="pendingJobInfo?.state === 'done'" class="px-4 py-2 apl-detail-section apl-section-done">
                  <div class="apl-section-head">
                    <i class="bi bi-check-circle-fill text-emerald-500" />
                    <span class="apl-section-title text-emerald-700">Job hoàn tất</span>
                    <span class="apl-pending-kind">{{ pendingJobInfo.kind }}</span>
                  </div>
                  <div class="apl-pending-body">
                    <!-- ALD 25/05/2026 - Inline media preview trong detail panel để user xem
                         ngay video/ảnh kết quả mà không cần mở canvas Output node hoặc
                         tab mới. video_url cho motion, image_url cho tryon-only. -->
                    <video
                      v-if="pendingJobInfo.video_url && !pendingJobInfo.is_image"
                      :src="pendingJobInfo.video_url"
                      controls
                      playsinline
                      preload="metadata"
                      class="w-full max-h-[420px] rounded-lg bg-black mb-2"
                    />
                    <img
                      v-else-if="pendingJobInfo.video_url && pendingJobInfo.is_image && pendingJobInfo.images.length <= 1"
                      :src="pendingJobInfo.video_url"
                      alt="Job output"
                      class="w-full max-h-[420px] object-cover object-top rounded-lg bg-gray-50 mb-2"
                    />
                    <div v-else-if="pendingJobInfo.images.length > 1" class="grid grid-cols-2 gap-2 mb-2">
                      <a
                        v-for="(img, idx) in pendingJobInfo.images"
                        :key="img.url || idx"
                        :href="img.url"
                        target="_blank"
                        class="block rounded-lg overflow-hidden bg-gray-50 border border-gray-200 hover:border-primary/60 transition"
                        :title="img.label || `Ảnh ${idx + 1}`"
                      >
                        <img :src="img.url" alt="Job output" class="w-full aspect-square object-cover" />
                        <span class="block px-2 py-1 text-[10px] text-gray-600 truncate">{{ img.label || `Ảnh ${idx + 1}` }}</span>
                      </a>
                    </div>
                    <div class="apl-pending-meta">
                      <span class="font-mono text-emerald-700">{{ pendingJobInfo.job_id?.slice(0, 8) }}</span>
                      <button
                        v-if="pendingJobInfo.video_url"
                        type="button"
                        class="ml-auto text-primary hover:underline text-xs"
                        @click="copyToClipboard(pendingJobInfo.video_url, 'Đã copy URL')"
                      >
                        <i class="bi bi-clipboard me-1" /> Copy URL
                      </button>
                      <a v-if="pendingJobInfo.video_url" :href="pendingJobInfo.video_url" target="_blank" download class="text-primary hover:underline text-xs">
                        <i class="bi bi-download me-1" /> Tải về
                      </a>
                    </div>
                  </div>
                </section>

                <!-- Error (priority — show first if failed) -->
                <section v-if="selectedTestRun.error" class="apl-detail-section apl-section-error">
                  <div class="apl-section-head">
                    <i class="bi bi-exclamation-triangle-fill text-rose-500" />
                    <span class="apl-section-title text-rose-700">Error</span>
                  </div>
                  <pre class="apl-detail-pre apl-pre-error">{{ selectedTestRun.error }}</pre>
                </section>

                <!-- Output: ẩn raw pending job khi entry đang running.
                     Drawer pending tracker phía dưới hiện progress đầy đủ rồi. -->
                <section v-if="selectedTestRun.output?.text && selectedRunStatus !== 'running'" class="apl-detail-section">
                  <div class="apl-section-head">
                    <i class="bi bi-arrow-return-right text-emerald-500" />
                    <span class="apl-section-title">Output</span>
                    <span class="apl-section-badge">{{ selectedTestRun.output.text.length.toLocaleString() }} chars</span>
                    <button type="button" class="apl-icon-btn-copy ml-auto" @click="copyToClipboard(selectedTestRun.output.text, 'Đã copy output')" title="Copy toàn bộ output">
                      <i class="bi bi-clipboard" />
                    </button>
                    <button type="button" class="apl-icon-btn-copy" @click="downloadOutput(selectedTestRun)" title="Tải về .txt">
                      <i class="bi bi-download" />
                    </button>
                    <button v-if="selectedTestRun.output.text.length > 5000" type="button" class="apl-icon-btn-copy" @click="outputExpanded = !outputExpanded" :title="outputExpanded ? 'Thu gọn' : 'Hiện đầy đủ (có thể chậm với file lớn)'">
                      <i :class="['bi', outputExpanded ? 'bi-arrows-collapse' : 'bi-arrows-expand']" />
                    </button>
                  </div>
                  <pre class="apl-detail-pre">{{ outputExpanded || selectedTestRun.output.text.length <= 5000
                    ? selectedTestRun.output.text
                    : selectedTestRun.output.text.slice(0, 5000) + '\n\n…(còn ' + (selectedTestRun.output.text.length - 5000).toLocaleString() + ' chars — bấm icon mở rộng hoặc tải .txt)' }}</pre>
                </section>

                <!-- Triggers (input config snapshot) -->
                <section v-if="selectedTestRun.triggers?.length" class="apl-detail-section">
                  <div class="apl-section-head">
                    <i class="bi bi-box-arrow-in-right text-emerald-500" />
                    <span class="apl-section-title">Inputs</span>
                  </div>
                  <div class="apl-triggers">
                    <div v-for="t in selectedTestRun.triggers" :key="t.nodeId" class="apl-trigger-row">
                      <span :class="['apl-trigger-source', `src-${t.source}`]">{{ t.source }}</span>
                      <span class="apl-trigger-type">{{ t.contentType }}</span>
                      <span class="apl-trigger-detail" :title="t.detail">{{ t.detail }}</span>
                    </div>
                  </div>
                  <pre v-if="selectedTestRun.input?.text" class="apl-detail-pre mt-2">{{ selectedTestRun.input.text }}</pre>
                </section>

                <!-- Log events -->
                <section class="apl-detail-section">
                  <div class="apl-section-head">
                    <i class="bi bi-list-ul text-gray-500" />
                    <span class="apl-section-title">Log</span>
                    <span class="apl-section-badge">{{ selectedTestRun.events?.length || 0 }} events</span>
                  </div>
                  <ul v-if="selectedTestRun.events?.length" class="apl-events">
                    <li v-for="(ev, idx) in selectedTestRun.events" :key="idx" class="apl-event">
                      <span :class="['apl-event-dot', `dot-${ev.level}`]">
                        <i v-if="ev.level === 'success'" class="bi bi-check-lg" />
                        <i v-else-if="ev.level === 'error'" class="bi bi-x-lg" />
                        <i v-else-if="ev.level === 'warn'" class="bi bi-exclamation" />
                        <i v-else class="bi bi-dot" />
                      </span>
                      <span class="apl-event-msg">{{ ev.msg }}</span>
                      <span class="apl-event-ts">{{ fmtRelTime(ev.ts, selectedTestRun.ts) }}</span>
                    </li>
                  </ul>
                  <p v-else class="apl-empty-text">Không có event</p>
                </section>
              </div>
            </div>
            <div v-else-if="testRunning" class="apl-history-empty">
              <div class="apl-loader-ring" />
              <p class="apl-empty-title mt-3">Đang chạy workflow...</p>
              <p class="apl-empty-hint">{{ fmtMs(runningElapsedMs) }} elapsed (cap 3 phút)</p>
            </div>
            <div v-else class="apl-history-empty">
              <i class="bi bi-clock-history text-3xl text-gray-300" />
              <p class="apl-empty-title mt-2">No Processing</p>
              <p class="apl-empty-hint">Bấm <kbd class="apl-kbd-mini"><i class="bi bi-play-fill" /> Run</kbd> trên thanh top để chạy workflow</p>
            </div>
          </div>
        </div>
      </Transition>
    </main>

    <!-- RIGHT — inspector. Animate width 0 → 360 khi click node. -->
    <aside :class="['apl-sidebar', 'apl-sidebar-right', selectedNode ? 'is-open' : '']">
      <Transition name="apl-inspector" mode="out-in">
        <div v-if="selectedNode" :key="selectedNode.id" class="apl-inspector-content">
          <div class="apl-inspector-header">
            <div class="flex items-center gap-3 flex-1 min-w-0">
              <span class="apl-inspector-icon" :style="{ background: currentNodeStyle.soft, color: currentNodeStyle.color }">
                <i :class="['bi text-lg', currentNodeStyle.icon]" />
              </span>
              <div class="min-w-0">
                <p class="apl-inspector-overline">Node</p>
                <p class="apl-inspector-title">{{ currentNodeStyle.label }}</p>
              </div>
            </div>
            <button type="button" class="apl-icon-btn apl-icon-btn-danger" @click="onDeleteNode" title="Xoá (⌫)">
              <i class="bi bi-trash" />
            </button>
            <!-- Mobile: nút đóng inspector (desktop bỏ chọn bằng click canvas, mobile bị overlay che) -->
            <button type="button" class="apl-inspector-close" @click="selectedNodeId = null" title="Đóng">
              <i class="bi bi-x-lg" />
            </button>
          </div>

          <div class="apl-inspector-body">
            <!-- #region ALD 14/06/2026 - Banner READ-ONLY khi xem run cũ (History snapshot): config đang xem là LÚC
                 RUN ĐÓ chạy, không phải bản đang sửa. Bấm "New workflow" (newBlankSession) để mở phiên soạn mới. -->
            <div v-if="isViewingHistory" class="flex items-center gap-2 mb-3 px-2 py-2 rounded-lg bg-amber-50 border border-amber-200 text-[11px] text-amber-800">
              <i class="bi bi-clock-history shrink-0 text-amber-500" />
              <span class="flex-1 leading-snug">Đang xem <b>run cũ</b> (chỉ đọc).</span>
              <button type="button" class="shrink-0 px-2 py-1 rounded-md bg-amber-500 text-white font-semibold hover:bg-amber-600 transition" @click="newBlankSession">New workflow</button>
            </div>
            <!-- #endregion -->
            <div class="apl-id-chip">
              <span class="text-[9px] uppercase tracking-wider text-gray-400 font-bold">Node ID</span>
              <code class="apl-id-code">{{ selectedNode.id }}</code>
            </div>

            <!-- ALD 14/06/2026 - Xem history = READ-ONLY: `inert` khoá HẲN tương tác (chuột + BÀN PHÍM + focus) toàn
                 bộ subtree config snapshot; pointer-events-none + mờ là fallback cho trình duyệt cũ. Banner + nút
                 "New workflow" ở trên KHÔNG nằm trong wrapper này nên vẫn bấm được. -->
            <div :inert="isViewingHistory" :class="isViewingHistory ? 'pointer-events-none opacity-60 select-none' : ''">
            <component
              :is="inspectorComponent(selectedNode.data.type)"
              v-if="inspectorComponent(selectedNode.data.type)"
              :config="selectedNode.data.config"
              :node-id="selectedNode.id"
              :node-type="selectedNode.data.type"
              :run-output="selectedNode.data._runOutput || {}"
              :runtime="nodeRuntime"
              @update:config="updateNodeConfig"
            />
            <p v-else class="text-xs text-gray-400 italic pt-2">Node này không có config — chỉ định nghĩa flow.</p>

            <!-- On Failure — common config cho mọi node trừ input/output/condition. -->
            <div v-if="canHaveErrorRoute(selectedNode.data.type)" class="apl-on-failure" :class="isViewingHistory ? 'pointer-events-none opacity-60' : ''">
              <label class="apl-of-label">
                <i class="bi bi-exclamation-triangle mr-1" />
                On Failure
              </label>
              <select :value="selectedNode.data.config?.onError || 'stop'" :disabled="isViewingHistory" class="apl-of-select mt-1.5" @change="onOnErrorChange($event.target.value)">
                <option value="stop">Stop workflow (default)</option>
                <option value="continue">Continue — bỏ qua node lỗi</option>
                <option value="route">Route to error branch</option>
              </select>
              <p v-if="(selectedNode.data.config?.onError || 'stop') === 'stop'" class="apl-of-hint">Lỗi → workflow dừng với status="error".</p>
              <p v-else-if="selectedNode.data.config?.onError === 'continue'" class="apl-of-hint">Lỗi → log warn, pass output node trước xuôi xuống (như chưa chạy node này).</p>
              <p v-else class="apl-of-hint">Lỗi → fire qua handle <b class="text-amber-700">ERR</b> (cam) bên phải. Wire tới node xử lý error (HTTP notify, fallback chat...).</p>
            </div>
            </div>
          </div>
        </div>
      </Transition>
    </aside>


    <!-- Workflow info modal -->
    <Transition enter-active-class="transition duration-200" leave-active-class="transition duration-150" enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="workflowInfoOpen" class="apl-modal-backdrop" @click.self="workflowInfoOpen = false">
        <div class="apl-modal">
          <div class="apl-modal-header">
            <div class="flex items-center gap-2">
              <span class="apl-modal-icon"><i class="bi bi-pencil-square" /></span>
              <div>
                <p class="apl-modal-overline">Workflow</p>
                <p class="apl-modal-title">Sửa thông tin</p>
              </div>
            </div>
            <button type="button" class="apl-icon-btn-modal" @click="workflowInfoOpen = false"><i class="bi bi-x-lg" /></button>
          </div>
          <div class="apl-modal-body space-y-3">
            <div>
              <label class="apl-modal-label">Tên hiển thị</label>
              <input v-model="workflowInfoForm.name" type="text" class="apl-modal-input mt-1.5" />
            </div>
            <div>
              <label class="apl-modal-label">Slug</label>
              <div class="mt-1.5 flex items-center gap-1.5">
                <span class="text-sm font-mono text-gray-400">/</span>
                <input v-model="workflowInfoForm.slug" type="text" class="apl-modal-input font-mono" @input="normalizeWorkflowInfoSlug" />
              </div>
              <p class="apl-modal-hint mt-1">Slug dùng cho URL/API endpoint. Đổi slug có thể ảnh hưởng chỗ đang gọi API cũ.</p>
            </div>
            <div>
              <label class="apl-modal-label">Mô tả</label>
              <textarea v-model="workflowInfoForm.description" rows="3" class="apl-modal-input mt-1.5" style="height:auto;resize:vertical" />
            </div>
          </div>
          <div class="apl-modal-footer">
            <button type="button" class="apl-btn apl-btn-ghost" @click="workflowInfoOpen = false">Huỷ</button>
            <button type="button" :disabled="workflowInfoSaving || !workflowInfoForm.slug || !workflowInfoForm.name" :class="['apl-btn', workflowInfoSaving ? 'apl-btn-disabled' : 'apl-btn-primary']" @click="saveWorkflowInfo">
              <i :class="['bi', workflowInfoSaving ? 'bi-arrow-repeat animate-spin' : 'bi-check2']" /> Lưu thông tin
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- #region ALD 22/05/2026 - API modal: Sync cURL + Async config + Async cURL/Callback preview -->
    <Transition enter-active-class="transition duration-200" leave-active-class="transition duration-150" enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="asyncModalOpen" class="apl-modal-backdrop" @click.self="asyncModalOpen = false">
        <div class="apl-modal apl-modal-xl max-h-11/12">
          <div class="apl-modal-header">
            <div class="flex items-center gap-2">
              <span class="apl-modal-icon"><i class="bi bi-terminal" /></span>
              <div>
                <p class="apl-modal-overline">API</p>
                <p class="apl-modal-title">/{{ workflow?.slug }}</p>
              </div>
            </div>
            <button type="button" class="apl-icon-btn-modal" @click="asyncModalOpen = false"><i class="bi bi-x-lg" /></button>
          </div>

          <div class="apl-modal-body apl-api-body">
            <!-- ── Section: Sync API (luôn hiện) ────────────────────────── -->
            <section class="apl-api-section">
              <header class="apl-api-section-head">
                <div>
                  <p class="apl-api-section-title">Sync API</p>
                  <p class="apl-api-section-sub">POST /invoke — chờ response chứa output luôn. Dùng cho workflow nhanh (&lt;30s).</p>
                </div>
                <button type="button" class="apl-icon-btn-copy" @click="copyToClipboard(syncCurlPreview, 'Đã copy Sync cURL')" title="Copy cURL">
                  <i class="bi bi-clipboard" />
                </button>
              </header>
              <pre class="apl-curl-pre">{{ syncCurlPreview }}</pre>
            </section>

            <!-- ── Section: Async API (toggle) ───────────────────────────── -->
            <section class="apl-api-section apl-api-section-async">
              <label class="apl-async-toggle">
                <input v-model="asyncConfig.async_enabled" type="checkbox" class="apl-checkbox" />
                <span class="min-w-0 flex-1">
                  <span class="apl-async-toggle-label">Async Mode</span>
                  <span class="apl-async-toggle-hint">App B gọi <code>/invoke-async</code> → nhận <code>job_id</code> ngay → App AI gọi callback khi xong.</span>
                </span>
              </label>

              <div v-if="asyncConfig.async_enabled" class="apl-async-body">
                <!-- Cấu hình -->
                <div class="apl-api-subhead">Cấu hình callback</div>
                <div class="space-y-3">
                  <div>
                    <label class="apl-modal-label">Callback URL <span class="text-rose-500">*</span></label>
                    <input
                      v-model="asyncConfig.callback_url"
                      type="text"
                      placeholder="https://app-b.pebsteel.com/api/ai-callback"
                      class="apl-modal-input mt-1.5 font-mono text-[12px]"
                    />
                    <p class="apl-modal-hint mt-1">Endpoint App B nhận POST khi workflow xong. Bắt đầu bằng <code>https://</code>.</p>
                  </div>

                  <div>
                    <label class="apl-modal-label">Custom headers</label>
                    <div class="apl-header-list mt-1.5">
                      <div v-for="(h, idx) in asyncConfig.callback_headers_list" :key="idx" class="apl-header-row">
                        <input
                          v-model="h.key"
                          type="text"
                          placeholder="Authorization"
                          class="apl-modal-input apl-header-key font-mono text-[12px]"
                        />
                        <input
                          v-model="h.value"
                          type="text"
                          placeholder="Bearer xxx"
                          class="apl-modal-input apl-header-val font-mono text-[12px]"
                        />
                        <button type="button" class="apl-icon-btn-mini" @click="removeHeader(idx)" title="Xoá header">
                          <i class="bi bi-x-lg" />
                        </button>
                      </div>
                      <button type="button" class="apl-btn apl-btn-ghost apl-btn-mini mt-1" @click="addHeader">
                        <i class="bi bi-plus-lg" /> Thêm header
                      </button>
                    </div>
                    <p class="apl-modal-hint mt-1">VD <code>Authorization: Bearer xxx</code> hoặc <code>X-API-Key: yyy</code> — App AI gửi y nguyên kèm callback, App B verify bằng middleware sẵn có.</p>
                  </div>
                </div>

                <!-- Preview 2 cột -->
                <div class="apl-api-subhead mt-4">Preview</div>
                <div class="apl-preview-grid mt-1.5">
                  <div class="apl-preview-block">
                    <div class="apl-preview-head">
                      <p class="apl-preview-title">cURL App B gửi</p>
                      <button type="button" class="apl-icon-btn-copy" @click="copyToClipboard(asyncCurlPreview, 'Đã copy Async cURL')" title="Copy">
                        <i class="bi bi-clipboard" />
                      </button>
                    </div>
                    <pre class="apl-curl-pre">{{ asyncCurlPreview }}</pre>
                  </div>
                  <div class="apl-preview-block">
                    <div class="apl-preview-head">
                      <p class="apl-preview-title">Callback App B nhận</p>
                      <button type="button" class="apl-icon-btn-copy" @click="copyToClipboard(asyncCallbackPreview, 'Đã copy callback payload')" title="Copy">
                        <i class="bi bi-clipboard" />
                      </button>
                    </div>
                    <pre class="apl-curl-pre">{{ asyncCallbackPreview }}</pre>
                  </div>
                </div>
                <p class="apl-modal-hint mt-2">Headers extra App B nhận: <code>X-Webhook-Job-Id</code>, <code>X-Webhook-Attempt</code> + custom headers ở trên. Retry tự động 3 lần (30s/2m/10m) nếu App B trả non-2xx.</p>
              </div>
            </section>
          </div>
          <div class="apl-modal-footer">
            <button type="button" class="apl-btn apl-btn-ghost" @click="asyncModalOpen = false">Đóng</button>
            <button
              type="button"
              :disabled="asyncSaving"
              :class="['apl-btn', asyncSaving ? 'apl-btn-disabled' : 'apl-btn-primary']"
              @click="saveAsyncConfig"
            >
              <i :class="['bi', asyncSaving ? 'bi-arrow-repeat animate-spin' : 'bi-check2']" /> Lưu config
            </button>
          </div>
        </div>
      </div>
    </Transition>
    <!-- #endregion -->

    <!-- Test Run Drawer -->
    <Transition enter-active-class="transition-opacity duration-200" leave-active-class="transition-opacity duration-150" enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="testRunOpen" class="fixed inset-0 z-[1000] bg-gray-950/45 backdrop-blur-sm" @click.self="testRunOpen = false" />
    </Transition>
    <Transition enter-active-class="transition-transform duration-220 ease-out" leave-active-class="transition-transform duration-180 ease-in" enter-from-class="translate-y-full" leave-to-class="translate-y-full">
      <div v-if="testRunOpen" class="fixed inset-x-0 bottom-0 z-[1001]">
        <div class="flex h-[min(82vh,900px)] w-full flex-col overflow-hidden rounded-t-[36px] border-t border-x-0 border-[color:var(--line)] bg-[color:var(--glass-bg-solid)] shadow-[0_-24px_64px_rgba(0,0,0,0.22)] backdrop-blur-xl pb-[env(safe-area-inset-bottom)]">
          <div class="mx-auto mt-3 h-1.5 w-12 rounded-full bg-gray-300/80" />
          <div class="flex items-center justify-between gap-3 border-b border-gray-100 bg-gray-50/70 px-5 py-4">
            <div class="flex min-w-0 items-center gap-2">
              <span class="apl-modal-icon"><i class="bi bi-play-fill" /></span>
              <div class="min-w-0">
                <p class="apl-modal-overline">Test workflow</p>
                <p class="apl-modal-title truncate">/{{ workflow?.slug }}</p>
              </div>
            </div>
            <button type="button" class="apl-icon-btn-modal" @click="testRunOpen = false"><i class="bi bi-x-lg" /></button>
          </div>
          <div class="flex-1 min-h-0 overflow-y-auto px-5 py-5">
            <div class="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
              <section class="rounded-2xl border border-[color:var(--line)] bg-gray-50/70 p-4">
                <div class="flex items-center justify-between gap-3">
                  <label class="apl-modal-label">
                    Chọn stream dữ liệu
                    <span class="text-gray-400 font-normal normal-case">— {{ sessionInputs.length }} node cần input</span>
                  </label>
                </div>
                <div class="apl-input-list mt-2.5">
                  <div v-for="input in sessionInputs" :key="input.id" class="apl-input-row">
                    <span class="apl-input-badge">session.{{ input.field }}</span>
                    <span class="apl-input-type">{{ input.contentType }}</span>
                  </div>
                </div>
                <p class="apl-modal-hint mt-3">
                  Các node `session.*` sẽ lấy dữ liệu từ payload này. Với `image/file`, hãy nối URL hoặc dùng Upload source trên canvas.
                </p>
              </section>

              <section class="rounded-2xl border border-[color:var(--line)] bg-gray-50 p-4">
                <div class="flex items-center justify-between gap-3">
                  <label class="apl-modal-label">Input test</label>
                  <span class="text-[11px] text-gray-400">⌘↵ để chạy</span>
                </div>
                <textarea
                  v-model="testInput"
                  rows="8"
                  placeholder="Nhập text giả lập user message..."
                  class="apl-modal-input mt-2.5 min-h-[180px]"
                  autofocus
                  @keydown.meta.enter.prevent="doTestRun"
                  @keydown.ctrl.enter.prevent="doTestRun"
                />
                <p class="apl-modal-hint">
                  Text này set vào <code>session.text</code>. Sau khi bấm Chạy, panel này sẽ chuyển sang drawer xử lý ở dưới để theo dõi progress.
                </p>
              </section>
            </div>
          </div>
          <div class="flex items-center justify-end gap-2 border-t border-gray-100 bg-gray-50/60 px-5 py-4">
            <button type="button" class="apl-btn apl-btn-ghost" @click="testRunOpen = false">Huỷ</button>
            <button type="button" class="apl-btn apl-btn-primary" @click="doTestRun">
              <i class="bi bi-play-fill" /> Chạy
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- #region ALD 30/06/2026 - Generator "Dựng từ kịch bản": kịch bản → AI Qwen tự gọi tool dựng node graph -->
    <Transition enter-active-class="transition-opacity duration-200" leave-active-class="transition-opacity duration-150" enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="moGenOpen" class="fixed inset-0 z-[1000] bg-gray-950/45 backdrop-blur-sm" @click.self="!moGen.loading && (moGenOpen = false)" />
    </Transition>
    <Transition enter-active-class="transition-transform duration-220 ease-out" leave-active-class="transition-transform duration-180 ease-in" enter-from-class="translate-y-full" leave-to-class="translate-y-full">
      <div v-if="moGenOpen" class="pointer-events-none fixed inset-0 z-[1001] flex items-end justify-center sm:items-center sm:p-4">
        <div
          :class="[
            'pointer-events-auto flex w-full flex-col overflow-hidden border border-[color:var(--line)] bg-[color:var(--glass-bg-solid)] shadow-[0_24px_64px_rgba(0,0,0,0.28)] backdrop-blur-xl pb-[env(safe-area-inset-bottom)] transition-[height,transform] duration-300 sm:max-w-2xl sm:pb-0',
            moGen.loading || moGenCollapsed
              ? 'rounded-t-[28px] sm:rounded-[24px]'
              : 'h-[88vh] rounded-t-[32px] sm:h-[min(82vh,820px)] sm:rounded-[24px]'
          ]"
        >
          <div class="mx-auto mt-3 h-1.5 w-12 rounded-full bg-gray-300/80 sm:hidden" />
          <div
            :class="[
              'flex items-center justify-between gap-3 bg-gray-50/70 px-5',
              moGen.loading || moGenCollapsed
                ? 'border-b-0 py-3'
                : 'border-b border-gray-100 py-4'
            ]"
          >
            <div class="flex min-w-0 items-center gap-2">
              <span class="apl-modal-icon"><i class="bi bi-magic" /></span>
              <div class="min-w-0">
                <p class="apl-modal-overline">AI Đạo diễn</p>
                <p class="apl-modal-title truncate">Dựng workflow từ kịch bản</p>
              </div>
            </div>
            <div class="flex items-center gap-2">
              <button
                v-if="!moGen.loading && moGenCollapsed"
                type="button"
                class="apl-icon-btn-modal"
                title="Mở rộng"
                @click="moGenCollapsed = false"
              >
                <i class="bi bi-arrows-angle-expand" />
              </button>
              <button type="button" class="apl-icon-btn-modal" :disabled="moGen.loading" @click="moGenOpen = false"><i class="bi bi-x-lg" /></button>
            </div>
          </div>
          <div v-if="moGen.loading || moGenCollapsed" class="px-5 pb-3 pt-1">
            <div class="flex w-full flex-col gap-3 border border-[color:var(--line)] bg-gray-100/95 px-4 py-3 shadow-[0_10px_30px_rgba(0,0,0,0.06)] md:flex-row md:items-center md:justify-between">
              <div class="flex min-w-0 items-center gap-3">
                <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-blue-50 text-blue-600">
                  <i :class="moGen.loading ? 'bi bi-arrow-repeat animate-spin' : 'bi bi-stars'" />
                </div>
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <p class="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-400">
                      {{ moGen.loading ? 'Processing' : 'Ready' }}
                    </p>
                    <span class="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-1 text-[11px] font-semibold text-blue-700">
                      <i class="bi bi-magic" />
                      {{ moGen.loading ? 'Submitting' : 'Collapsed' }}
                    </span>
                  </div>
                  <p class="truncate text-sm font-semibold leading-5 text-gray-900">
                    {{ moGen.loading ? 'Motions AI đang dựng workflow và tự kéo node lên canvas' : 'Drawer đã thu gọn sau khi submit' }}
                  </p>
                  <p class="text-xs leading-4 text-gray-500">
                    {{ moGen.loading ? 'Đợi thinking, tool-call và kết quả stream cập nhật xong.' : 'Bấm mở rộng nếu muốn xem hoặc sửa lại kịch bản.' }}
                  </p>
                </div>
              </div>
              <div class="flex shrink-0 flex-wrap items-center gap-2 text-xs text-gray-500">
                <span class="inline-flex items-center gap-1 rounded-full bg-gray-50 px-2 py-1">
                  <i class="bi bi-dot text-blue-500 text-base leading-none" />
                  Thinking
                </span>
                <span class="inline-flex items-center gap-1 rounded-full bg-gray-50 px-2 py-1">
                  <i class="bi bi-dot text-blue-500 text-base leading-none" />
                  Tool-call
                </span>
                <span class="inline-flex items-center gap-1 rounded-full bg-gray-50 px-2 py-1">
                  <i class="bi bi-dot text-blue-500 text-base leading-none" />
                  Canvas update
                </span>
              </div>
            </div>
          </div>
          <!-- CHAT đạo diễn: AI hỏi → user trả lời/chọn/upload ngay trong khung chat (ALD 30/06/2026) -->
          <div v-else class="flex min-h-0 flex-1 flex-col">
            <div ref="moChatScroll" class="flex-1 min-h-0 space-y-4 overflow-y-auto px-5 py-5">
              <div v-for="(m, i) in moGen.chat" :key="i" class="flex" :class="m.role === 'user' ? 'justify-end' : 'justify-start'">
                <div v-if="m.role === 'ai'" class="flex max-w-[88%] items-start gap-2">
                  <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-600"><i class="bi bi-stars" /></div>
                  <div class="rounded-2xl rounded-tl-sm bg-gray-100 px-3.5 py-2.5 text-sm leading-relaxed text-gray-800" v-html="moFmt(m.text)" />
                </div>
                <div v-else class="max-w-[88%] rounded-2xl rounded-tr-sm bg-blue-600 px-3.5 py-2.5 text-sm text-white">
                  <img v-if="m.image" :src="m.image" alt="" class="mb-1.5 max-h-36 rounded-lg" >
                  <span class="whitespace-pre-line break-words">{{ m.text }}</span>
                </div>
              </div>
              <div v-if="moGen.busy" class="flex items-center gap-2 pl-[42px] text-sm text-gray-400">
                <span class="flex gap-1">
                  <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-300" style="animation-delay:0ms" />
                  <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-300" style="animation-delay:140ms" />
                  <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-300" style="animation-delay:280ms" />
                </span>
                đạo diễn đang soạn…
              </div>
            </div>

            <!-- Thanh nhập thích ứng theo câu hỏi đang chờ -->
            <div class="border-t border-gray-100 bg-gray-50 px-4 py-3">
              <!-- options (chọn 1 + Khác) -->
              <div v-if="moGen.pending && moGen.pending.kind === 'options'" class="flex flex-wrap items-center gap-2">
                <button v-for="opt in moGen.pending.options" :key="opt.value" type="button"
                  class="rounded-full border border-gray-200 bg-gray-50 px-3.5 py-2 text-sm font-medium text-gray-700 transition hover:border-blue-400 hover:bg-blue-50 hover:text-blue-700"
                  @click="moPick(opt)">{{ opt.label }}</button>
                <template v-if="moGen.pending.allowOther">
                  <input v-if="moGen.otherActive" v-model="moGen.draft" type="text" class="apl-modal-input min-w-[160px] flex-1" placeholder="Nhập câu trả lời…" @keydown.enter="moSendText()" >
                  <button v-else type="button" class="rounded-full border border-dashed border-gray-300 px-3.5 py-2 text-sm text-gray-500 transition hover:border-blue-400 hover:text-blue-700" @click="moGen.otherActive = true"><i class="bi bi-pencil mr-1" />Khác…</button>
                  <button v-if="moGen.otherActive" type="button" class="apl-btn apl-btn-primary" :disabled="!moGen.draft.trim()" @click="moSendText()">Gửi</button>
                </template>
              </div>

              <!-- asset (upload / URL / bỏ qua) -->
              <div v-else-if="moGen.pending && moGen.pending.kind === 'asset'" class="flex flex-wrap items-center gap-2">
                <label class="flex cursor-pointer items-center gap-1.5 rounded-full border border-gray-300 px-3.5 py-2 text-sm font-medium text-gray-700 transition hover:border-blue-400 hover:text-blue-700" :class="moGen.uploadingChat ? 'opacity-60 pointer-events-none' : ''">
                  <i :class="moGen.uploadingChat ? 'bi bi-arrow-repeat animate-spin' : 'bi bi-upload'" /> {{ moGen.uploadingChat ? 'Đang tải…' : 'Tải ảnh' }}
                  <input type="file" accept="image/*" class="hidden" :disabled="moGen.uploadingChat" @change="moUpload($event)" >
                </label>
                <input v-model="moGen.draft" type="url" class="apl-modal-input min-w-[160px] flex-1" placeholder="hoặc dán URL ảnh…" @keydown.enter="moSendUrl()" >
                <button type="button" class="apl-btn apl-btn-primary" :disabled="!moGen.draft.trim()" @click="moSendUrl()">Gửi</button>
                <button v-if="moGen.pending.allowSkip" type="button" class="apl-btn apl-btn-ghost" @click="moSkip()">Bỏ qua</button>
              </div>

              <!-- textarea (kịch bản / nhập tự do) -->
              <div v-else-if="moGen.pending && moGen.pending.kind === 'textarea'" class="space-y-2">
                <!-- ALD 03/07/2026 - kịch bản mẫu "quảng cáo công dụng sản phẩm" (mẫu cầm SP → dùng thử → CTA) -->
                <button v-if="moGen.pending.key === 'script'" type="button"
                  class="inline-flex items-center gap-1.5 rounded-full border border-dashed border-blue-300 bg-blue-50/60 px-3.5 py-1.5 text-xs font-medium text-blue-700 transition hover:border-blue-400 hover:bg-blue-50"
                  @click="moGen.draft = MO_SAMPLE_SCRIPT"><i class="bi bi-magic" />Dùng kịch bản mẫu: quảng cáo công dụng sản phẩm</button>
                <div class="flex items-end gap-2">
                  <textarea v-model="moGen.draft" rows="2" class="apl-modal-input flex-1 resize-none !text-[13px]" :placeholder="moGen.pending.placeholder || 'Nhập…'" @keydown.enter.exact.prevent="moSendText()" />
                  <button type="button" class="apl-btn apl-btn-primary shrink-0" :disabled="!moGen.draft.trim()" @click="moSendText()"><i class="bi bi-send" /></button>
                </div>
              </div>

              <!-- đang dựng / xong -->
              <div v-else class="flex items-center justify-between">
                <span class="inline-flex items-center gap-2 text-sm text-gray-400">
                  <i v-if="moGen.loading" class="bi bi-arrow-repeat animate-spin" />
                  {{ moGen.loading ? 'Đang dựng phim…' : 'Hội thoại kết thúc' }}
                </span>
                <div class="flex items-center gap-2">
                  <button v-if="!moGen.loading" type="button" class="apl-btn apl-btn-ghost" @click="moChatStart()"><i class="bi bi-arrow-clockwise" /> Làm lại</button>
                  <button type="button" class="apl-btn apl-btn-ghost" :disabled="moGen.loading" @click="moGenOpen = false">Đóng</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <!-- #endregion -->
    </Transition>

  </div>
  <!-- #endregion -->
</template>

<script setup>
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import { markRaw } from 'vue'
import { isEqual } from 'lodash-es'
import FlowNode from '~/components/workflow/FlowNode.vue'

definePageMeta({ middleware: 'auth', layout: 'default' })

const route = useRoute()
const wf = useWorkflows()
const toast = useToast()
const noti = useNotifications()
const confirmDialog = useConfirm()

const workflow = ref(null)
// ALD 27/05/2026 - Public workflow của user khác: ẩn nút Lưu + badge "Chưa lưu",
// chỉ cho phép Chạy. BE trả `owned` flag từ GET /workflows/:id (so user_id với session).
// Strict check === true → loading state và non-owner đều fall vào false → ẩn UI sửa đổi.
// Owner thấy nút Lưu sau khi fetch xong (vài chục ms), không flash UX đáng kể.
const isOwned = computed(() => workflow.value?.owned === true)
const isSingerWorkflow = computed(() => String(workflow.value?.slug || '').toLowerCase() === 'singer')
const nodes = ref([])
const edges = ref([])
const selectedNodeId = ref(null)
const saving = ref(false)
const workflowInfoOpen = ref(false)
const workflowInfoSaving = ref(false)
const workflowInfoForm = reactive({ name: '', slug: '', description: '' })
// Saved baseline — deep snapshot. dirty = !isEqual(currentDefinition(), savedDefinition).
// Tránh flag-based "đã thay đổi" sai (vd select node, scroll Vue Flow trigger watch).
const savedDefinition = ref(null)
const paletteSearch = ref('')
// #region ALD 07/07/2026 - Mobile: gom node-list (sidebar trái) thành drawer overlay cho gọn.
// Desktop (≥820px) giữ nguyên cột 220px cố định; dưới 820px sidebar thành off-canvas + nút toggle.
const paletteOpen = ref(false)
const isMobile = ref(false)
onMounted(() => {
  const mq = window.matchMedia('(max-width: 819px)')
  const upd = () => { isMobile.value = mq.matches; if (!mq.matches) paletteOpen.value = false }
  upd()
  mq.addEventListener('change', upd)
  onBeforeUnmount(() => mq.removeEventListener('change', upd))
})
// #endregion
const testRunOpen = ref(false)
const testInput = ref('')
const testRunning = ref(false)
// Test history per workflow — localStorage. Mỗi entry: { id, ts, input, status, output, events, snapshot, triggers }
const testHistory = ref([])
const selectedRunId = ref(null)
const drawerVisible = ref(false)   // mặc định ẩn — toggle qua nút "Runs" trên topbar
// ALD 27/05/2026 - Drag-to-resize drawer (port từ pebsteel-ai). Persist localStorage,
// min 200px, max 80% viewport. Cursor ns-resize trên top edge.
const DRAWER_HEIGHT_KEY = 'pebsteel.motions.workflowDrawerHeight'
const drawerHeight = ref(360)
if (import.meta.client) {
  const saved = Number(localStorage.getItem(DRAWER_HEIGHT_KEY))
  if (saved >= 200 && saved <= 1200) drawerHeight.value = saved
}
function startDrawerResize(e) {
  const startY = e.clientY
  const startH = drawerHeight.value
  function onMove(ev) {
    const maxH = Math.floor(window.innerHeight * 0.8)
    drawerHeight.value = Math.min(maxH, Math.max(200, startH + (startY - ev.clientY)))
  }
  function onUp() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.userSelect = ''
    localStorage.setItem(DRAWER_HEIGHT_KEY, String(drawerHeight.value))
  }
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}
// #region ALD 22/05/2026 - Test runs: filter + auto-select + current-running event
const runFilter = ref('all')   // 'all' | 'success' | 'error'
const outputExpanded = ref(false)  // false = hiển thị 5000 chars đầu; true = full
function isLiveJobMeta(m) {
  if (!m || !m.job_id) return false
  if (m.kind && m.kind !== 'motion') return false
  if (m.job_status === 'queued' || m.job_status === 'running') return true
  return m.pending === true && !m.video && !m.image && !m.images?.length && m.job_status !== 'error' && m.job_status !== 'cancelled'
}
function effectiveRunStatus(run) {
  if (!run) return null
  const m = run.output?.metadata
  if (isLiveJobMeta(m)) return 'running'
  if (run.status === 'success') return 'success'
  if (run.status === 'queued' || run.status === 'running') return 'running'
  return 'error'
}
const filteredHistory = computed(() => {
  if (runFilter.value === 'all') return testHistory.value
  return testHistory.value.filter((r) => effectiveRunStatus(r) === runFilter.value)
})
const runCounts = computed(() => ({
  all: testHistory.value.length,
  success: testHistory.value.filter((r) => effectiveRunStatus(r) === 'success').length,
  error: testHistory.value.filter((r) => effectiveRunStatus(r) === 'error').length,
  running: testHistory.value.filter((r) => effectiveRunStatus(r) === 'running').length
}))
const selectedTestRun = computed(() => testHistory.value.find((r) => r.id === selectedRunId.value))
const selectedRunStatus = computed(() => effectiveRunStatus(selectedTestRun.value))
// ALD 24/05/2026 - Bug fix: trước có { immediate: true } → load workflow là tự bind
// run cũ vào canvas. User chỉ muốn canvas clean khi mở; vào Lịch sử mới chọn manual.
// Vẫn auto-select khi run đang select bị xoá (selectedRunId không tồn tại nữa).
watch(testHistory, (val) => {
  if (val.length === 0) { selectedRunId.value = null; return }
  // Chỉ re-select nếu selectedRunId hiện tại invalid — không tự chọn từ null
  if (selectedRunId.value && !val.find((r) => r.id === selectedRunId.value)) {
    selectedRunId.value = val[0].id
  }
})
// Filter chip change: chỉ re-select khi đang có entry selected nhưng bị filter loại bỏ
watch(filteredHistory, (val) => {
  if (selectedRunId.value && val.length > 0 && !val.find((r) => r.id === selectedRunId.value)) {
    selectedRunId.value = val[0].id
  }
})
// Reset outputExpanded mỗi khi chọn run khác (file lớn mặc định thu gọn)
watch(selectedRunId, () => { outputExpanded.value = false })

// ALD 27/05/2026 - Lazy-load events/output khi click run đã done. /workflows/:id/runs
// endpoint trả lightweight (chỉ status + dates) để tránh 25MB payload với 50 runs ×
// 500KB events. Khi user click 1 entry trong drawer, fetch detail qua /runs/:id rồi
// merge events + output + input. Tránh fetch lại nếu entry đã có events.
// Cuối cùng trigger reconcile cho entry này nếu output còn pending — cover case user
// click vào entry Motion Transfer async đã xong ở BE nhưng workflow_runs
// frozen ở pending state. Reconcile sẽ fetch motion job status
// thật + patch output.metadata.video URL.
watch(selectedRunId, async (runId) => {
  if (!runId) return
  const entry = testHistory.value.find((r) => r.id === runId)
  if (!entry || !entry._runId) return
  // ALD 18/06/2026 - Entry running: list endpoint KHÔNG trả events/definition, nên TRƯỚC
  // đây chỉ trông vào poll loop để bind. Nhưng loop có thể chưa attach (run start ở tab
  // khác / sau mount / reload mà resume miss) → click vào job đang chạy ra "0 events / ?
  // nodes". Fix: fetch detail NGAY để bind, rồi đảm bảo poll loop chạy tiếp (guard double).
  if (effectiveRunStatus(entry) === 'running' || entry._live) {
    try {
      const detail = await wf.getRun(entry._runId)
      if (detail) {
        patchEntry(runId, {
          events: detail.events || entry.events || [],
          output: detail.output ?? entry.output,
          input:  detail.input  ?? entry.input,
          definition: detail.definition ?? entry.definition,   // để dựng snapshot graph + đếm node
          status: detail.status || entry.status,
        })
      }
    } catch (e) {
      console.warn('[history] running fetch fail:', e?.message)
    }
    if (entry._runId && !_activePolls.has(runId)) pollRunUntilDone(runId, entry._runId, entry.ts)
    return
  }
  // Lazy load events/definition nếu chưa có (definition cần để dựng snapshot graph read-only)
  if (!Array.isArray(entry.events) || entry.events.length === 0 || !entry.definition) {
    try {
      const detail = await wf.getRun(entry._runId)
      if (detail) {
        patchEntry(runId, {
          events: detail.events || [],
          output: detail.output ?? entry.output,
          input:  detail.input  ?? entry.input,
          error:  detail.error_msg ?? entry.error,
          definition: detail.definition ?? entry.definition,   // snapshot graph để xem read-only
        })
      }
    } catch (e) {
      console.warn('[history] lazy fetch detail fail:', e?.message)
    }
  }
  // Re-reconcile nếu entry Motion Transfer vẫn còn pending sau lazy load.
  const cur = testHistory.value.find((r) => r.id === runId)
  const m = cur?.output?.metadata
  if (m && m.job_id && m.kind === 'motion' && (m.pending || !m.video) && !m.image) {
    reconcileStalePendingJobs().catch((e) => console.warn('[history] reconcile fail:', e?.message))
  }
})

// #region ALD 31/05/2026 - History snapshot: xem run cũ → dựng lại ĐÚNG graph lúc đó (read-only).
// Trước đây chiếu kết quả lên canvas SỐNG → sai (canvas thêm node sau khi chạy → run cũ "mọc"
// node thừa). Chọn run done có definition → swap canvas sang snapshot của run; thoát → khôi phục.
const _liveDef = ref(null)         // backup canvas đang sửa (chỉ lưu khi lần đầu vào history)
const _snapshotRunId = ref(null)   // run đang được dựng snapshot lên canvas
function _runDefToNodes(def) {
  return (def?.nodes || []).map((n) => {
    const config = normalizeMotionSegmentConfig(n.type, { ...(n.data?.config || {}) })
    if (n.data?.label && !config.label) config.label = n.data.label
    if (n.data?.purpose && !config.purpose) config.purpose = n.data.purpose
    return { id: n.id, type: 'step', position: n.position || { x: 100, y: 100 }, data: { type: n.type, config } }
  })
}
function _runDefToEdges(def) {
  return (def?.edges || []).map((e) => ({ ...e, label: e.data?.label || undefined, class: edgeClassFromLabel(e.data?.label), type: 'step' }))
}
function _enterSnapshot(run) {
  if (_snapshotRunId.value === run.id) return       // đã dựng rồi → bỏ qua (watcher fire nhiều lần)
  if (!_liveDef.value) _liveDef.value = currentDefinition()  // lưu canvas sống lần đầu
  nodes.value = _runDefToNodes(run.definition)
  edges.value = _runDefToEdges(run.definition)
  _snapshotRunId.value = run.id
}
function _exitSnapshot() {
  if (!_liveDef.value) return
  nodes.value = _runDefToNodes(_liveDef.value)
  edges.value = _runDefToEdges(_liveDef.value)
  _liveDef.value = null
  _snapshotRunId.value = null
}
// #endregion

// ALD 24/05/2026 - Khi user click chọn run khác trong drawer history, project state
// của run đó vào canvas: output node hiện video/metadata của run, mỗi node lấy
// _runState theo events node_id (success/warn/error), Motion Transfer lấy progress.
// Dep gồm cả output + events + status của run đã chọn → re-fire khi reconcileStalePendingJobs
// hoặc pollPendingMotion patch entry (output.metadata.video flip từ null → URL).
watch([
  selectedRunId,
  () => testHistory.value.length,
  () => selectedTestRun.value?.output,
  () => selectedTestRun.value?.events,
  () => selectedTestRun.value?.status,
  () => selectedTestRun.value?.definition,
], () => {
  const run = selectedTestRun.value
  if (!run) {
    _exitSnapshot()   // thoát history → khôi phục canvas đang sửa
    // Không có run nào — reset tất cả node về idle
    for (const n of nodes.value) {
      if (n.data._runState || n.data._runOutput) {
        n.data = { ...n.data, _runState: null, _runOutput: null }
      }
    }
    return
  }
  // Read-only view của run: dựng lại ĐÚNG graph của run TỪ definition (snapshot) → projection iterate đúng
  // tập node của run nên fill data/preview chuẩn theo node-id.
  // ALD 20/06/2026 - TRƯỚC chỉ snapshot khi run DONE; run đang chạy giữ "canvas sống" → khi reload/quay lại
  // (canvas sống chưa khớp hoặc khác graph run) projection trượt node-id → "running KHÔNG fill data" (done thì
  // fill vì được dựng lại từ definition). Giờ snapshot cho CẢ run đang chạy MIỄN có definition → running fill
  // giống done. Không có definition (run quá cũ / chưa fetch kịp) → giữ canvas sống như trước.
  if (run.definition && (run.definition.nodes || []).length) _enterSnapshot(run)
  else _exitSnapshot()
  // Map node_id → outcome based on events trong run
  const eventsByNode = new Map()
  for (const ev of (run.events || [])) {
    if (!ev.node_id) continue
    const arr = eventsByNode.get(ev.node_id) || []
    arr.push(ev)
    eventsByNode.set(ev.node_id, arr)
  }
  // ALD 27/05/2026 - Inputs upload URL extraction: worker emits "Uploaded model → URL"
  // ở handler tryon/motion. Parse events của node consumer (tryon/motion) tìm các URL
  // này, map theo handle name (model/product/motion/audio) → tìm input node upstream
  // qua edges → set _runOutput URL để FlowNode render preview. Workflow_runs strip
  // staticData (base64 → empty) khi save nên cần URL từ worker upload.
  const inputUrlsByNodeId = new Map()
  // Build edge map: consumer_node_id → { handle → upstream_node_id }
  const upstreamByHandle = new Map()
  for (const e of (edges.value || [])) {
    if (!e.targetHandle) continue
    const m = upstreamByHandle.get(e.target) || {}
    m[e.targetHandle] = e.source
    upstreamByHandle.set(e.target, m)
  }
  // Parse "Uploaded <field> → <url>" from event messages on consumer nodes
  const uploadRe = /Uploaded\s+(\w+)\s+→\s+(https?:\/\/\S+)/
  // ALD 28/05/2026 - Input nodes tự emit URL với 2 pattern khác (không qua consumer):
  //   "Static image/video/audio: <filename> → URL"  (handleInput source=static path)
  //   "Library /audio: <filename> → URL"            (handleInput source=library path)
  // Match cho cả 2 → set vào inputUrlsByNodeId cho chính input node (KHÔNG cần upstream
  // lookup). Trước đây bị bỏ sót → input-motion (video MP4) + input-audio không hiện
  // preview khi click history dù events có URL.
  const inputSelfRe = /(?:Static\s+(?:image|video|audio|file)|Library\s+\/\w+):.+?(https?:\/\/\S+)/
  const publicHostForRewrite = (typeof window !== 'undefined' && window.location?.origin?.includes('localhost'))
    ? '' : (useRuntimeConfig?.()?.public?.motionBackendUrl || '')
  const rewriteUrl = (raw) => (publicHostForRewrite ? raw.replace(/https?:\/\/kong(?::\d+)?/i, publicHostForRewrite.replace(/\/$/, '')) : raw)
  for (const [consumerId, evs] of eventsByNode) {
    // (A) Consumer parse: "Uploaded model/product → URL" → set upstream input node URL
    const handles = upstreamByHandle.get(consumerId)
    if (handles) {
      for (const ev of evs) {
        const match = uploadRe.exec(ev.msg || '')
        if (!match) continue
        const [, field, rawUrl] = match
        const upstreamId = handles[field]
        if (!upstreamId) continue
        inputUrlsByNodeId.set(upstreamId, rewriteUrl(rawUrl))
      }
    }
    // (B) Input self-emit parse: input node's own events có URL → set cho chính nó
    for (const ev of evs) {
      const match = inputSelfRe.exec(ev.msg || '')
      if (!match) continue
      if (inputUrlsByNodeId.has(consumerId)) continue  // ưu tiên URL từ consumer (A)
      inputUrlsByNodeId.set(consumerId, rewriteUrl(match[1]))
    }
  }
  // (C) ALD 30/05/2026 - Nguồn TIN CẬY nhất: output.metadata chứa sẵn {handle}_url
  // (model_url/product_url/motion_url/audio_url) — KHÔNG phụ thuộc parse chuỗi log (vốn
  // hay miss). Map qua handle → upstream input node. Fix bug: click history input
  // image/video không fill / fill sai khi switch run.
  const outMetaForInputs = run.output?.metadata || {}
  for (const [consumerId, handleMap] of upstreamByHandle) {
    void consumerId
    for (const handle in handleMap) {
      const upId = handleMap[handle]
      if (inputUrlsByNodeId.has(upId)) continue
      const u = outMetaForInputs[`${handle}_url`]
      if (u && /^https?:\/\//.test(String(u))) inputUrlsByNodeId.set(upId, rewriteUrl(String(u)))
    }
  }
  for (const n of nodes.value) {
    const events = eventsByNode.get(n.id) || []
    const lastLevel = events.length ? events[events.length - 1].level : null
    let runState = null
    const runStatus = effectiveRunStatus(run)
    if (runStatus === 'running') {
      runState = events.length ? 'running' : null
    } else if (lastLevel === 'error') runState = 'error'
    else if (lastLevel === 'warn') runState = 'warn'
    else if (events.some((e) => e.level === 'success')) runState = 'success'
    let runOutput = null
    // ALD 27/05/2026 - Input nodes: lấy URL upload đã extract từ events (workflow def
    // mất staticData khi save). Map type (image/video/audio) đúng key cho FlowNode preview.
    const inputType = n.data?.type
    const isInputNode = inputType === 'input' || inputType === 'inputText' || inputType === 'inputImage' || inputType === 'inputFile' || inputType === 'inputHistory'
    if (isInputNode && inputUrlsByNodeId.has(n.id)) {
      const url = inputUrlsByNodeId.get(n.id)
      const ct = n.data?.config?.contentType || 'image'
      const key = ct === 'video' ? 'video' : ct === 'audio' ? 'audio' : 'image'
      runOutput = { ...(runOutput || {}), [key]: url, _restoredFromRun: true }
    }
    // Output node lấy final run.output
    if (n.data.type === 'output' && run.output) {
      const m = run.output.metadata || {}
      runOutput = {
        video: m.video || null,
        videos: Array.isArray(m.videos) ? m.videos : (m.video ? [{ url: m.video }] : []),  // ALD 03/06/2026 - đa preset
        image: m.image || null,
        images: Array.isArray(m.images) ? m.images : (m.image ? [{ url: m.image }] : []),
        pending: !!m.pending,
        progress: m.progress || 0,
        current_step: m.current_step || '',
        job_status: m.job_status || runStatus,
        job_id: m.job_id || null,
        aspect_ratio: m.aspect_ratio || null,
        quality: m.quality || null,
      }
      // ALD 30/05/2026 - Chỉ coi là 'running' khi pending VÀ CHƯA có output. Trước đây
      // job done (đã có video/image) nhưng metadata.pending còn stale (reconcile chưa kịp
      // flip) → vẫn hiện processing. Có output = done, bất kể pending flag.
      if (m.pending && !m.video && !m.image && !m.images?.length) runState = 'running'
    }
    // Debug node: lấy extra từ last info event (engine summary)
    if (n.data.type === 'debug' && events.length) {
      const last = events[events.length - 1]
      if (last.extra) runOutput = { ...(runOutput || {}), ...last.extra }
    }
    // ALD 27/05/2026 - Tryon / Motion intermediate nodes: scan events
    // tìm event "giàu" nhất chứa previewUrl (engine mới) hoặc metadata.{image,video,tryon_url}
    // (engine cũ qua handleDebug summary). Trước đây không apply → canvas idle khi click
    // history entry cũ, dù events array có đầy đủ thông tin. Cover cả 2 shape.
    if ((n.data.type === 'tryon' || n.data.type === 'create-image' || n.data.type === 'compose' || n.data.type === 'motion') && events.length) {
      let found = null
      for (let i = events.length - 1; i >= 0; i--) {
        const ex = events[i].extra
        if (!ex) continue
        // ALD 27/05/2026 - Skip events có uploadedFor: đó là upload event cho upstream
        // input node (model/product/motion), không phải output của consumer. Trước đây
        // pick last event với previewUrl → ăn URL product → Tryon node hiện thumbnail
        // product trong lúc chưa chạy xong.
        if (ex.uploadedFor) continue
        if (ex.previewUrl) {
          const kind = ex.previewKind === 'video' ? 'video' : 'image'
          found = {
            [kind]: ex.previewUrl,
            metadata: ex.outputMeta || {},
          }
          break
        }
        if (ex.metadata && (ex.metadata.image || ex.metadata.video || ex.metadata.tryon_url || ex.metadata.images?.length)) {
          found = {
            image: ex.metadata.image || ex.metadata.tryon_url || ex.metadata.images?.[0]?.url || null,
            video: ex.metadata.video || null,
            images: Array.isArray(ex.metadata.images) ? ex.metadata.images : [],
            metadata: ex.metadata,
          }
          break
        }
      }
      if (found) runOutput = { ...(runOutput || {}), ...found }
    }
    n.data = { ...n.data, _runState: runState, _runOutput: runOutput }
  }
})
// Hiển thị 1-line error preview trong list item
function errorPreview(err) {
  if (!err) return ''
  return String(err).split('\n')[0].slice(0, 80)
}
// Hiển thị 1-line output preview
function outputPreview(text) {
  if (!text) return ''
  const s = String(text).replace(/\s+/g, ' ')
  const m = s.match(/^\[(\w+)\][\s]+motion(?:-transfer)?\b/i)
  if (m) {
    const st = m[1].toLowerCase()
    if (st === 'pending' || st === 'queued') return 'Motion Transfer · đang chờ worker…'
    if (st === 'running') return 'Motion Transfer · đang xử lý…'
  }
  return s.slice(0, 80)
}
// Re-run với cùng input đã test (nhưng definition latest từ canvas).
// ALD 24/05/2026 - Confirm trước khi re-run vì job nặng (6-22 phút) + tốn GPU. Tránh
// accidental click (focus stuck trên button + Enter → kick job ngoài ý muốn).
async function rerunFromHistory(run) {
  const ok = await confirmDialog.ask({
    title: 'Chạy lại workflow?',
    message: 'Sẽ tạo run mới với cùng input. Job nặng (~6-22 phút GPU).',
    confirmText: 'Chạy lại',
    cancelText: 'Huỷ',
    variant: 'primary',
  })
  if (!ok) return
  testInput.value = run?.input?.text || ''
  doTestRun()
}
// ALD 17/06/2026 - "Tiếp tục từ chỗ lỗi": chạy lại nhưng DÙNG LẠI node đã render xong (cache theo nội dung) →
// chỉ render node lỗi + phía sau. pendingResume gửi resume=true cho /test; doTestRun đọc rồi reset (mặc định = chạy mới).
const pendingResume = ref(false)
async function resumeFromHistory(run) {
  const ok = await confirmDialog.ask({
    title: 'Tiếp tục từ chỗ lỗi?',
    message: 'Dùng lại các bước đã render xong (ảnh/clip cũ), chỉ render lại node lỗi + phía sau. Nhanh hơn nhiều so với chạy mới.',
    confirmText: 'Tiếp tục',
    cancelText: 'Huỷ',
    variant: 'primary',
  })
  if (!ok) return
  pendingResume.value = true
  testInput.value = run?.input?.text || ''
  doTestRun()
}
// Elapsed time của running test (re-eval mỗi giây qua reactive ref)
const _now = ref(Date.now())
let _nowTimer
watch(testRunning, (running) => {
  if (running) {
    _now.value = Date.now()
    _nowTimer = setInterval(() => { _now.value = Date.now() }, 1000)
  } else {
    clearInterval(_nowTimer); _nowTimer = null
  }
})
onBeforeUnmount(() => {
  clearInterval(_nowTimer)
  if (motionStreamInstance) motionStreamInstance.unsubscribeAll()
})
const runningStartTs = ref(0)
const runningElapsedMs = computed(() => testRunning.value && runningStartTs.value ? _now.value - runningStartTs.value : 0)
// #endregion

// Computed dirty — deep-equal current vs saved. KHÔNG dùng flag-based vì
// Vue Flow trigger nodes.value mutations (selection, drag end) → false positive.
// ALD 24/05/2026 - Strip staticData (base64) khỏi cả 2 vế khi compare dirty. staticData
// chỉ là session-local preview, không persist BE → không nên ảnh hưởng dirty flag.
function _normForDirty(def) {
  if (!def) return def
  // ALD 24/05/2026 - JSON deep clone (not structuredClone) vì definition có thể chứa
  // Vue reactive proxy / File ref → DataCloneError. JSON.stringify strip non-serializable.
  const clone = JSON.parse(JSON.stringify(def))
  for (const n of clone.nodes || []) {
    const c = n.data?.config
    if (c && c.staticData) c.staticData = ''
  }
  return clone
}
const dirty = computed(() => {
  if (!savedDefinition.value) return false
  return !isEqual(_normForDirty(currentDefinition()), _normForDirty(savedDefinition.value))
})

const { addNodes, addEdges, project, addSelectedNodes, removeSelectedNodes, getNodes, fitView } = useVueFlow()

// ALD 15/06/2026 - SS cổng động: valid handle = N cổng đầu của [input, image2, image3] theo config.inputCount.
function _validSsHandles(c) {
  const n = Math.max(1, Math.min(3, Number(c?.inputCount) || 1))
  return new Set(['input', 'image2', 'image3'].slice(0, n))
}
function pruneDanglingEdges() {
  const byId = new Map(nodes.value.map((n) => [n.id, n]))
  const kept = edges.value.filter((e) => {
    if (!e.targetHandle) return true
    const tn = byId.get(e.target)
    const tt = tn?.data?.type
    if (tt === 'ss') return _validSsHandles(tn.data.config).has(e.targetHandle)
    // ALD 03/07/2026 - wan-i2v tắt toggle "Ảnh cuối" → cổng 'end' biến mất → tự gỡ dây đã nối vào 'end'.
    if (tt === 'wan-i2v' && e.targetHandle === 'end') return tn.data.config?.endEnabled !== false
    return true
  })
  if (kept.length !== edges.value.length) edges.value = kept
}
let _pruneT = null
watch(nodes, () => { clearTimeout(_pruneT); _pruneT = setTimeout(pruneDanglingEdges, 250) }, { deep: true })

// ALD 28/05/2026 - History view = READ-ONLY. Khi user click 1 history entry, canvas
// auto-fill data của run đó (qua projection watcher). Lúc này KHÔNG cho phép Save (vì
// data trên canvas là snapshot run cũ, save sẽ overwrite workflow def với data đó) và
// KHÔNG cho phép Run (input đã pre-filled từ history, không phải session mới).
// "New Session" button: deselect history → projection reset → user upload lại + edit.
const isViewingHistory = computed(() => Boolean(selectedRunId.value))



// ALD 24/05/2026 - Cmd/Ctrl+A select all nodes. Esc bỏ chọn. Vue Flow chỉ cung cấp
// rubber-band selection mặc định — keyboard shortcut tự implement.
function selectAllNodes(e) {
  // Đang focus input/textarea → KHÔNG preventDefault, để Cmd+A chọn chữ native.
  const ae = typeof document !== 'undefined' ? document.activeElement : null
  if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.isContentEditable)) return
  // Chỉ chặn default + chọn tất cả node khi đang ở canvas (không ở ô nhập liệu).
  e?.preventDefault()
  const all = getNodes.value
  if (!all.length) return
  addSelectedNodes(all)
}
function clearSelection() {
  const sel = getNodes.value.filter((n) => n.selected)
  if (sel.length) removeSelectedNodes(sel)
  selectedNodeId.value = null
}
const customNodeTypes = { step: markRaw(FlowNode) }

// Apple system colors per node
// ALD 15/06/2026 - Gom lại 7 nhóm theo CHỨC NĂNG cho gọn (trước: nhóm "Motion" phình 12 node trộn lẫn).
// `hidden:true` = ẩn khỏi palette (không kéo mới được) NHƯNG vẫn nằm trong ALL_TYPES + còn handler/inspector
// → workflow đã lưu dùng node đó vẫn hiển thị & chạy. 'compose' ẩn vì cùng backend Create Image (preset trùng).
const CATEGORIES = [
  {
    id: 'io', label: 'Nguồn / Kết quả',
    nodes: [
      { id: 'input',       label: 'Input Text',  hint: 'Text input (session / URL / static)',    icon: 'bi-chat-left-text', color: '#34C759', soft: 'rgba(52,199,89,0.15)' },
      { id: 'input-image', label: 'Input Image', hint: 'Ảnh — upload device hoặc URL',           icon: 'bi-image',          color: '#34C759', soft: 'rgba(52,199,89,0.15)' },
      { id: 'input-video', label: 'Input Video', hint: 'Video — upload device hoặc URL',         icon: 'bi-film',           color: '#34C759', soft: 'rgba(52,199,89,0.15)' },
      { id: 'input-audio', label: 'Input Audio', hint: 'Audio (MP3/WAV/M4A) — upload hoặc URL', icon: 'bi-music-note-beamed', color: '#34C759', soft: 'rgba(52,199,89,0.15)' },
      { id: 'input-file',  label: 'Input File',  hint: 'File generic — PDF/ZIP/v.v.',            icon: 'bi-file-earmark',   color: '#34C759', soft: 'rgba(52,199,89,0.15)' },
      { id: 'output',      label: 'Output',      hint: 'Trả kết quả về end user',                icon: 'bi-box-arrow-right', color: '#8E8E93', soft: 'rgba(255,255,255,0.08)' }
    ]
  },
  {
    id: 'image', label: 'Ảnh',
    nodes: [
      { id: 'create-image', label: 'Create Image', hint: 'Qwen-Edit / Gemini · prompt + 1–3 ảnh tham chiếu → ảnh mới (ETA ~30s)', icon: 'bi-images', color: '#AF52DE', soft: 'rgba(175,82,222,0.16)' },
      { id: 'edit-image', label: 'Sửa ảnh', hint: 'Qwen-Edit / Gemini · list ảnh có sẵn + mô tả → sửa TỪNG ảnh, giữ nguyên bố cục/nhân dạng (mỗi ảnh tối đa 5 version, hiện dần khi xong)', icon: 'bi-pencil-square', color: '#5AC8FA', soft: 'rgba(90,200,250,0.15)' },
      // ALD 03/07/2026 - gỡ 'Đặt sản phẩm' khỏi palette (dư — thay bằng node "Sửa ảnh" chế độ GHÉP ảnh).
      // Node product-overlay trong workflow CŨ đã lưu vẫn render + chạy bình thường (FlowNode/worker giữ nguyên).
      { id: 'cast-model',  label: 'Tuyển mẫu (kho)', hint: 'Khi CHỈ có ảnh sản phẩm: tự chọn 1 người mẫu từ kho (Settings) theo giới tính + độ tuổi, dùng cố định cho cả phim. Nối ra Sửa ảnh (ghép)/tryon.', icon: 'bi-people-fill', color: '#0A84FF', soft: 'rgba(10,132,255,0.15)' },
      { id: 'tryon',        label: 'Try-on',       hint: 'Qwen-Edit / Gemini · model + product → ảnh đã thay đồ (ETA ~30s)', icon: 'bi-person-vcard', color: '#FF9500', soft: 'rgba(255,149,0,0.15)' },
      // ẩn: cùng backend create-image (preset "ghép người vào mẫu"). Giữ handler cho workflow cũ.
      { id: 'compose', label: 'Ghép vào mẫu', hidden: true, hint: 'Ghép người thật vào ảnh mẫu (bối cảnh/pose đẹp) · Qwen-Edit · ảnh mẫu + 1–2 người, giữ mặt', icon: 'bi-person-bounding-box', color: '#5856D6', soft: 'rgba(88,86,214,0.16)' }
    ]
  },
  {
    id: 'video', label: 'Video từ ảnh / prompt',
    nodes: [
      { id: 'motion',         label: 'Motion Transfer', hint: 'Wan 2.2 Animate · ref image + motion video',                       icon: 'bi-film',         color: '#FF2D55', soft: 'rgba(255,45,85,0.15)' },
      { id: 'teen-flycam',    label: 'Teen Flycam', hint: '1 ảnh người mẫu → social video 10s / 5 shot, flycam + pose tự nhiên', icon: 'bi-camera-video', color: '#FF2D55', soft: 'rgba(255,45,85,0.15)' },
      { id: 'trend-tiktok',   label: 'Trend TikTok', hidden: true, hint: '2 ảnh before/after → clip trend theo preset mẫu TikTok', icon: 'bi-stars', color: '#FF2D55', soft: 'rgba(255,45,85,0.15)' },
      // ALD 03/07/2026 - đổi tên 'Video AI' → 'LoRA' (engine LTX + LoRA custom) để phân biệt với node Wan mới.
      { id: 'ss',             label: 'LoRA',             hint: 'LTX-2.3 + LoRA custom. I2V: Ảnh→Video. T2V: Text→Video. V2V: Video→Restyle. Chọn mode + LoRA trong Inspector.', icon: 'bi-film', color: '#5856D6', soft: 'rgba(88,86,214,0.16)' },
      { id: 'wan-i2v',        label: 'Ảnh → Video', hint: 'Nối 1 ẢNH + prompt → video chuyển động. Provider: Wan 2.1/2.2 self-host (miễn phí) hoặc DashScope cloud (happyhorse / wan2.7-i2v có audio + ảnh cuối, cần API key).', icon: 'bi-camera-reels', color: '#FF2D55', soft: 'rgba(255,45,85,0.15)' },
      { id: 'text-to-video',  label: 'Text → Video',    hint: 'Prompt → video ngắn (Wan2.2 T2V / LTX). Không cần ảnh.', icon: 'bi-camera-reels', color: '#FF2D55', soft: 'rgba(255,45,85,0.15)' },
    ]
  },
  {
    id: 'talk', label: 'Người nói (lip-sync)',
    nodes: [
      { id: 'talk',        label: 'Nói (lip-sync)',  hint: 'MultiTalk · ảnh nhân vật + câu thoại (giọng nam/nữ/clone) → video NÓI nhép miệng đúng khẩu hình.', icon: 'bi-mic-fill', color: '#34C759', soft: 'rgba(52,199,89,0.15)' },
      { id: 'voiceover',   label: 'Lồng tiếng (đọc mô tả)', hint: 'Nối 1 CLIP video (từ SS/Ảnh→Video) + lời thuyết minh + giọng → giọng đọc tiếng Việt ghép lên clip, giữ nguyên hình & độ dài. KHÔNG cần khuôn mặt.', icon: 'bi-soundwave', color: '#34C759', soft: 'rgba(52,199,89,0.15)' }
    ]
  },
  {
    id: 'film', label: 'Dựng phim / ghép',
    nodes: [
{ id: 'concat',     label: 'Ghép cảnh',     hint: 'Ghép ≥2 phân cảnh (clip video) thành 1 video, GIỮ tiếng từng cảnh. Nối cổng clip1, clip2… từ các node talk/motion.', icon: 'bi-collection-play-fill', color: '#5856D6', soft: 'rgba(88,86,214,0.16)' },
      { id: 'reveal',     label: 'Đè lộ',         hint: 'Đè 2 video CÙNG người (khác bộ đồ): dải mềm quét top→bottom lộ dần "Đồ mới" (B) lên "Nền" (A). Nối 2 video cùng cỡ/động tác (vd 2 nhánh Motion cùng driver+seed). Pure ffmpeg, nhanh, không GPU.', icon: 'bi-layers-half', color: '#5856D6', soft: 'rgba(88,86,214,0.16)' },
      { id: 'subtitle',   label: 'Phụ đề + Dịch', hint: '1 video → nhận lời thoại (Whisper) + dịch. Chế độ: cháy PHỤ ĐỀ (giữ tiếng gốc) / LỒNG TIẾNG Việt (thay giọng) / cả hai. Dịch từng câu realtime. Nối 1 node Input (Video).', icon: 'bi-badge-cc', color: '#FF9500', soft: 'rgba(255,149,0,0.15)' },
      { id: 'enhance',       label: 'Nâng chất lượng', hint: 'Nâng nét VIDEO hoặc ẢNH (chọn mode trong Inspector). Video: upscale → 1080p/2K + nội suy fps (RIFE). Ảnh: ESRGAN ×4 → 2K/4K. Chạy SAU Wan, tự xả GPU/RAM rồi dồn full tài nguyên.', icon: 'bi-badge-hd', color: '#FF9500', soft: 'rgba(255,149,0,0.15)' },

    ]
  },
  {
    id: 'tools', label: 'Tiện ích / Luồng',
    nodes: [
      { id: 'http',        label: 'HTTP',        hint: 'Gọi REST API ngoài',              icon: 'bi-cloud-arrow-up-fill',    color: '#5856D6', soft: '#ECEBFB' },
      // ALD 11/06/2026 - Node khai báo API key theo provider: nối cổng ra → cổng "API Key" của node đích (ưu
      // tiên nhất), hoặc đặt rời trên canvas = tự phân bổ cho mọi node cùng provider. Chỉ Self-host miễn key.
      { id: 'api-key',     label: 'API Key',     hint: 'Khai báo API key (Gemini / Veo / custom). Nối vào cổng API Key của node dùng provider đó, hoặc đặt rời = tự phân bổ theo Type. Self-host không cần key.', icon: 'bi-key-fill', color: '#FFCC00', soft: 'rgba(255,204,0,0.15)' },
      { id: 'condition',   label: 'Condition',   hint: 'If-else theo expression',         icon: 'bi-shuffle',                color: '#FF9500', soft: 'rgba(255,149,0,0.15)' },
      { id: 'validate',    label: 'Validate',    hint: 'Check field + math sau LLM',      icon: 'bi-check2-square',          color: '#7CDDAA', soft: 'rgba(52,199,89,0.18)' },
      { id: 'debug',       label: 'Debug',       hint: 'Pass-through + log preview giữa stages', icon: 'bi-bug-fill',         color: '#FF9500', soft: 'rgba(255,149,0,0.15)' },
      { id: 'gpu-warmup',  label: 'GPU Warmup',  hint: 'Chuẩn bị vRAM trước OCR',         icon: 'bi-lightning-charge-fill',  color: '#34C759', soft: 'rgba(52,199,89,0.15)' },
      { id: 'gpu-free',    label: 'GPU Free',    hint: 'Đợi vRAM giải phóng sau OCR',     icon: 'bi-arrow-down-circle-fill', color: '#8E8E93', soft: 'rgba(255,255,255,0.08)' },
      { id: 'workflow',    label: 'Workflow',    hint: 'Gọi workflow khác (nested)',      icon: 'bi-diagram-3-fill',         color: '#FF2D55', soft: 'rgba(255,45,85,0.15)' }
    ]
  }
]
const ALL_TYPES = CATEGORIES.flatMap((c) => c.nodes)

const filteredCategories = computed(() => {
  const q = paletteSearch.value.trim().toLowerCase()
  // ALD 15/06/2026 - ẩn node có hidden:true khỏi palette (vẫn còn trong ALL_TYPES + handler cho workflow cũ).
  return CATEGORIES
    .map((c) => ({ ...c, nodes: c.nodes.filter((n) => !n.hidden && (!q || n.label.toLowerCase().includes(q) || n.hint.toLowerCase().includes(q))) }))
    .filter((c) => c.nodes.length > 0)
})

const selectedNode = computed(() => nodes.value.find((n) => n.id === selectedNodeId.value))

// ALD 24/05/2026 - Runtime data prop cho inspectors (vd InspectorDebug). Combine
// events từ selectedTestRun + output cuối + duration của node hiện tại.
const nodeRuntime = computed(() => {
  if (!selectedNode.value) return {}
  const run = selectedTestRun.value
  const nodeId = selectedNode.value.id
  const nodeEvents = (run?.events || []).filter((e) => e.node_id === nodeId)
  // ALD 25/05/2026 - Chỉ output của NODE NÀY. KHÔNG fallback run.output (= final workflow
  // output = Motion Transfer pending) — Debug node ở giữa chain sẽ show Motion Transfer
  // pending thay vì Tryon URL.
  // ALD 27/05/2026 - Robust fallback: tìm event "giàu" nhất (có previewUrl / outputMeta
  // từ engine mới, hoặc metadata.{model,product,tryon}_url từ handleDebug summary cũ).
  // Engine cũ emit success không có extra → last event = success → null. Engine mới
  // emit success kèm extra. Cả 2 shape đều được map về { metadata } để InspectorDebug
  // render previewGrid.
  let fallback = null
  for (let i = nodeEvents.length - 1; i >= 0; i--) {
    const ex = nodeEvents[i].extra
    if (!ex) continue
    if (ex.outputMeta || ex.previewUrl) {
      fallback = { metadata: ex.outputMeta || {}, image: ex.previewKind !== 'video' ? ex.previewUrl : undefined, video: ex.previewKind === 'video' ? ex.previewUrl : undefined }
      break
    }
    if (ex.metadata && (ex.metadata.tryon_url || ex.metadata.model_url || ex.metadata.image || ex.metadata.video)) {
      fallback = { metadata: ex.metadata, text: ex.text }
      break
    }
  }
  const nodeOutput = selectedNode.value.data._runOutput || fallback || null
  return {
    output: nodeOutput,
    events: nodeEvents,
    durationMs: run?.durationMs,
  }
})

// ALD 24/05/2026 - Pending job info extract từ selectedTestRun.output.metadata
// để render Pending Job tracker section trong drawer detail. Live progress qua poll.
// ALD 24/05/2026 - Job info luôn return có state field. Template branch state để render
// layout riêng cho running / cancelled / error / done thay vì reuse 1 layout + chỉ đổi
// status flag.
const pendingJobInfo = computed(() => {
  const m = selectedTestRun.value?.output?.metadata
  if (!m || !m.job_id) return null
  const runStatus = selectedRunStatus.value
  let state = 'running'
  if (m.job_status === 'cancelled' || runStatus === 'cancelled') state = 'cancelled'
  else if (m.job_status === 'error' || (runStatus === 'error' && m.pending !== true)) state = 'error'
  // ALD 30/05/2026 - done khi CÓ output (video HOẶC image — tryon trả image), hoặc
  // job_status='done', hoặc workflow run success mà không còn pending async. Trước đây
  // chỉ xét m.video → tryon (image) kẹt mãi 'running' dù đã có output.png.
  else if (m.video || m.image || m.images?.length || m.job_status === 'done' || (runStatus === 'success' && !m.pending)) state = 'done'
  const images = Array.isArray(m.images) ? m.images : (m.image ? [{ url: m.image }] : [])
  return {
    state,
    job_id: m.job_id,
    kind: 'Motion Transfer',
    progress: m.progress || 0,
    current_step: m.current_step || '',
    video_url: m.video || m.image || images[0]?.url || null,
    images,
    // ALD 25/05/2026 - is_image cho tryon-only path (Stage 1 stop_after_tryon) → render
    // <img> thay vì <video> trong detail panel. metadata.image set khi extension là PNG/JPG.
    is_image: (!!m.image || images.length > 0) && !m.video,
    eta: state === 'running' && m.progress ? estimateEta(m.progress) : '',
    error: selectedTestRun.value?.error || '',
  }
})
function estimateEta(progress) {
  if (!progress || progress >= 1) return ''
  // Giả định tổng ~12 phút cho 30s-720p preset
  const totalMin = 12
  const remainMin = totalMin * (1 - progress)
  return remainMin < 1 ? '<1 phút' : `~${Math.ceil(remainMin)} phút`
}

// ALD 13/07/2026 - Cancel Motion Transfer job.
async function onCancelMotionJob(jobId) {
  if (!jobId) return
  const ok = await useConfirm().ask({
    title: 'Huỷ job đang chạy?',
    message: 'Worker sẽ stop và xóa job. Không thể khôi phục.',
    confirmText: 'Huỷ job',
    variant: 'danger',
  })
  if (!ok) return
  try {
    const auth = useAuth()
    const entry = testHistory.value.find((r) => r.output?.metadata?.job_id === jobId)
    await auth.apiFetch(`/functions/v1/motion-jobs/${jobId}`, { method: 'DELETE' })
    toast.success('Đã yêu cầu huỷ job', { duration: 3000 })
    if (motionStreamInstance) motionStreamInstance.unsubscribe(jobId)
    // Mark entry locally
    if (entry) {
      patchEntry(entry.id, {
        status: 'error',
        error: 'Cancelled bởi user',
        output: { ...entry.output, metadata: { ...entry.output.metadata, pending: false, job_status: 'cancelled' } },
      })
    }
  } catch (e) {
    toast.error(`Không huỷ được: ${e?.message || e}`, { duration: 5000 })
  }
}
const currentNodeStyle = computed(() => ALL_TYPES.find((t) => t.id === selectedNode.value?.data?.type) || { color: '#8E8E93', soft: 'rgba(255,255,255,0.08)', icon: 'bi-circle', label: '?' })

function miniMapColor(node) {
  return ALL_TYPES.find((t) => t.id === node.data?.type)?.color || '#8E8E93'
}

function edgeClassFromLabel(label) {
  return {
    true: 'edge-true', false: 'edge-false',
    success: 'edge-success', error: 'edge-error'
  }[label] || ''
}

function normalizeWorkflowInfoSlug() {
  workflowInfoForm.slug = String(workflowInfoForm.slug || '')
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

function openWorkflowInfo() {
  if (!workflow.value) return
  workflowInfoForm.name = workflow.value.name || ''
  workflowInfoForm.slug = workflow.value.slug || ''
  workflowInfoForm.description = workflow.value.description || ''
  workflowInfoOpen.value = true
}

async function saveWorkflowInfo() {
  if (workflowInfoSaving.value || !workflow.value) return
  normalizeWorkflowInfoSlug()
  if (!workflowInfoForm.slug || !workflowInfoForm.name.trim()) {
    toast.error('Vui lòng nhập tên và slug workflow')
    return
  }
  workflowInfoSaving.value = true
  try {
    const patch = {
      name: workflowInfoForm.name.trim(),
      slug: workflowInfoForm.slug.trim(),
      description: workflowInfoForm.description || ''
    }
    const updated = await wf.update(route.params.id, patch)
    workflow.value = { ...workflow.value, ...patch, ...(updated || {}) }
    workflowInfoOpen.value = false
    toast.success('Đã cập nhật thông tin workflow')
  } catch (err) {
    toast.error(err?.data?.error || err?.message || 'Cập nhật workflow thất bại')
  } finally {
    workflowInfoSaving.value = false
  }
}

async function deleteWorkflow() {
  if (!workflow.value) return
  const ok = await confirmDialog.ask({
    title: `Xoá workflow /${workflow.value.slug}?`,
    message: 'Workflow, lịch sử run và endpoint API liên quan sẽ bị xoá. Hành động này không hoàn tác.',
    confirmText: 'Xoá',
    cancelText: 'Huỷ',
    variant: 'danger',
  })
  if (!ok) return
  try {
    await wf.remove(route.params.id)
    toast.success(`Đã xoá /${workflow.value.slug}`)
    await navigateTo('/workflows')
  } catch (err) {
    toast.error(err?.data?.error || err?.message || 'Xoá workflow thất bại')
  }
}

onMounted(async () => {
  // ALD 24/05/2026 - Bug fix order: load workflow def TRƯỚC rồi mới load test history.
  // Trước đó: loadTestHistory chạy song song với wf.get → testHistory watcher fire khi
  // nodes.value vẫn empty → projection watcher iterate 0 nodes → output node không nhận
  // _runState='running'/_runOutput → reload bị mất binding yellow border + video preview.
  const wfData = await wf.get(route.params.id)
  if (needsSingerRepair(wfData)) {
    const fixed = { ...wfData, definition: buildSingerMotionDefinition() }
    workflow.value = await wf.update(route.params.id, { definition: fixed.definition }).catch(() => fixed)
  } else {
    workflow.value = wfData
  }
  if (!workflow.value) return
  const def = workflow.value.definition || { nodes: [], edges: [] }
  nodes.value = (def.nodes || []).map((n) => {
    // ALD 27/05/2026 - Migration seed (021-023) lưu label/purpose ở `data.label` và
    // `data.purpose` (ngoài config). Inspector đọc từ `config.label` → empty UI.
    // Normalize ngược vào config nếu chưa có. New nodes (drag-drop) lưu trong config
    // từ đầu nên không bị ảnh hưởng. Đảm bảo source-of-truth duy nhất = config.
    const config = normalizeMotionSegmentConfig(n.type, { ...(n.data?.config || {}) })
    if (n.data?.label && !config.label) config.label = n.data.label
    if (n.data?.purpose && !config.purpose) config.purpose = n.data.purpose
    return {
      id: n.id,
      type: 'step',
      position: n.position || { x: 100, y: 100 },
      data: { type: n.type, config }
    }
  })
  edges.value = (def.edges || []).map((e) => ({
    ...e,
    label: e.data?.label || undefined,
    class: edgeClassFromLabel(e.data?.label),
    type: 'step'
  }))
  await nextTick()
  pruneDanglingEdges()   // cắt edge trỏ cổng đã biến mất (ss thay đổi inputCount) → dây không neo vào giữa node
  savedDefinition.value = currentDefinition()
  // Sau khi nodes populated: load history (projection watcher sẽ bind đúng) + resume polls
  await loadTestHistory()
  resumeLivePolls()
})

// (dirty là computed deep-equal, không cần watch trigger flag)

function onDragStart(ev, type) {
  ev.dataTransfer.setData('application/vueflow', type)
  ev.dataTransfer.effectAllowed = 'move'
}

// ALD 07/07/2026 - Tách logic tạo node từ palette để onDrop (desktop kéo-thả) + onPaletteItemTap
// (mobile drawer, chạm để thêm) dùng chung. position = toạ độ FLOW đã project.
function createPaletteNode(paletteId, position) {
  // ALD 24/05/2026 - Palette variants input-image / -video / -audio / -file đều map về type='input',
  // chỉ khác default contentType. Audio + File mặc định source='library' (pick từ /audio /storage),
  // Image + Video mặc định source='static' (upload device).
  const variantMap = { 'input-image': 'image', 'input-video': 'video', 'input-audio': 'audio', 'input-file': 'file' }
  const type = variantMap[paletteId] ? 'input' : paletteId
  const id = `${paletteId}-${Date.now().toString(36)}`
  let config
  if (variantMap[paletteId]) {
    const ct = variantMap[paletteId]
    const libraryDefault = ct === 'audio' || ct === 'file'
    config = libraryDefault
      ? { contentType: ct, source: 'library', field: ct, libraryId: '' }
      : { contentType: ct, source: 'static',  field: ct, staticData: '', staticMime: '', staticName: '' }
  } else {
    config = defaultConfig(type)
  }
  addNodes([{ id, type: 'step', position, data: { type, config } }])
  selectedNodeId.value = id
}

function onDrop(ev) {
  ev.preventDefault()
  if (isViewingHistory.value) return // xem lại history = read-only, không thêm node
  const paletteId = ev.dataTransfer.getData('application/vueflow')
  if (!paletteId) return
  const bounds = ev.currentTarget.getBoundingClientRect()
  const position = project({ x: ev.clientX - bounds.left, y: ev.clientY - bounds.top })
  createPaletteNode(paletteId, position)
}

// ALD 07/07/2026 - Mobile: palette là drawer overlay, không kéo-thả được bằng touch → CHẠM để thêm node
// vào GIỮA canvas hiện tại rồi đóng drawer. Chỉ kích hoạt ở mobile (isMobile) để desktop giữ nguyên kéo-thả.
function onPaletteItemTap(paletteId) {
  if (isViewingHistory.value) return
  const el = typeof document !== 'undefined' ? document.querySelector('.apl-canvas') : null
  const b = el?.getBoundingClientRect()
  const position = b ? project({ x: b.width / 2, y: b.height / 2 }) : project({ x: 200, y: 200 })
  createPaletteNode(paletteId, position)
  paletteOpen.value = false
}


// #region ALD 24/06/2026 - Generator "Multi-outfit Motion": ghép Try-on → Motion Transfer → video dài đổi nhiều outfit.
// Ý tưởng: 1 VIDEO MOTION GỐC (driver) + N ảnh outfit (mỗi ref = người mẫu đã mặc 1 look). Mỗi ref → 1 node Motion
// (Wan Animate) áp lên 1 ĐOẠN driver liên tiếp (ref1 = 0–5s, ref2 = 5–10s, …) → concat = clip dài như gốc nhưng
// đổi outfit, motion LIỀN MẠCH (vì các đoạn driver nối tiếp nhau). Cắt đoạn driver do worker làm theo
// driverStartSec/driverDurSec trong config node (mediaViaJob đổ config → params job).
const moGenOpen = ref(false)
const moGenCollapsed = ref(false)
const moChatScroll = ref(null)
// ALD 30/06/2026 - mở modal → bắt đầu hội thoại chat đạo diễn mới.
// ALD 03/07/2026 - mở LẠI thì GIỮ hội thoại cũ (trước đây reset mỗi lần mở → dựng xong workflow là "mất" chat);
// chỉ bắt đầu phiên mới khi chưa có chat. Muốn chat mới → nút "Làm lại" ở cuối hội thoại.
// Nạp thư viện giọng clone cho câu hỏi "chọn giọng" (im lặng nếu lỗi — vẫn còn giọng mặc định).
watch(moGenOpen, (v) => { if (v) { moVoicesLib.load().catch(() => {}); if (!moGen.chat.length) moChatStart() } })
const moGenJsonTemplate = `{
  "prompt": "Hãy phân tích kịch bản quảng cáo sau thành storyboard chuẩn để dùng với AI khác. Trả về JSON thuần, không markdown. Mỗi cảnh có: id, timeRange, title, visual, action, voice, textOverlay, durationSec. Giữ tiếng Việt cho voice/textOverlay, mô tả visual/action ngắn gọn và rõ ràng.",
  "schema": {
    "version": "1.0",
    "type": "product-ad-storyboard",
    "fields": ["id", "timeRange", "title", "visual", "action", "voice", "textOverlay", "durationSec"]
  },
  "segments": [
    {
      "id": 1,
      "timeRange": "00s-05s",
      "title": "Hook",
      "visual": "Mô tả hình ảnh chính của cảnh",
      "action": "Diễn xuất / chuyển động",
      "voice": "Lời thoại hoặc voiceover",
      "textOverlay": ["Text 1", "Text 2"],
      "durationSec": 5
    }
  ]
}`
// ALD 30/06/2026 - step: 'script' (nhập kịch bản) → 'interview' (đạo diễn hỏi ngược) → dựng. questions/answers/otherText
// cho bước phỏng vấn; hasModelImage/hasProductImage để đạo diễn hỏi đúng (bỏ hỏi nguồn người mẫu nếu đã có ảnh).
const moGen = reactive({ script: '', inputMode: 'text', aspectRatio: '9:16', maxScenes: 6, think: false, loading: false, modelLabel: 'Qwen',
  step: 'script', interviewing: false, questions: [], answers: {}, otherText: {}, hasModelImage: false, hasProductImage: false,
  // ALD 30/06/2026 - asset người dùng đưa (nạp sẵn vào workflow) + chiến lược người mẫu.
  productAsset: null, productUrl: '', productUploading: false,
  modelStrategy: 'library', modelAsset: null, modelUrl: '', modelUploading: false,
  // ALD 30/06/2026 - chat đạo diễn (hội thoại hỏi-đáp dẫn tới dựng phim).
  chat: [], collected: { answers: {} }, queue: [], pending: null, draft: '', otherActive: false, busy: false, uploadingChat: false })
const moStorageFiles = useStorageFiles()
// Upload ảnh asset (sản phẩm/mẫu) → storage → giữ {url,path,bucket} để BE nạp sẵn vào input-image.
async function uploadMoAsset(ev, which) {
  const file = ev.target?.files?.[0]
  if (!file) return
  const flag = which === 'product' ? 'productUploading' : 'modelUploading'
  moGen[flag] = true
  try {
    const res = await moStorageFiles.uploadFile(file, { bucket: 'chat-attachments', prefix: 'wf-asset' })
    if (res?.signedUrl && res?.path) {
      const asset = { kind: 'upload', url: res.signedUrl, path: res.path, bucket: res.bucket || 'chat-attachments', name: file.name, mime: file.type }
      if (which === 'product') { moGen.productAsset = asset; moGen.productUrl = '' }
      else { moGen.modelAsset = asset; moGen.modelUrl = '' }
    }
  } catch (e) {
    toast.error('Upload ảnh lỗi: ' + (e?.data?.error || e?.message || ''))
  } finally {
    moGen[flag] = false
    if (ev.target) ev.target.value = ''
  }
}
// Gom asset → payload gửi BE (ưu tiên file đã upload, rồi URL dán tay). null nếu không có.
function moAssetPayload(which) {
  const asset = which === 'product' ? moGen.productAsset : moGen.modelAsset
  const url = String((which === 'product' ? moGen.productUrl : moGen.modelUrl) || '').trim()
  if (asset && (asset.path || asset.url)) return asset
  if (/^https?:\/\//i.test(url)) return { kind: 'url', url }
  return null
}
const moGenPlaceholder = computed(() => (
  moGen.inputMode === 'json'
    ? 'Dán JSON storyboard chuẩn ở đây. Có thể copy mẫu JSON bên trên để dùng với AI khác hoặc chỉnh tay rồi submit.'
    : `Ví dụ:
PHÂN CẢNH 1 (0–6s)
Hình ảnh: Người mẫu cầm serum, đưa gần camera.
Hành động: Mỉm cười, đưa sản phẩm lên ngang mặt.
Voice: "Hãy nhắn tin ngay để được tư vấn sản phẩm phù hợp với làn da của bạn."`
))

async function copyMoGenJsonTemplate() {
  try {
    await copyText(moGenJsonTemplate)
    toast.success('Đã sao chép mẫu JSON')
  } catch {
    toast.error('Không thể sao chép JSON, hãy thử lại')
  }
}
const MULTI_OUTFIT_MOTION_CONFIG = {
  preset: '5s-720p',
  mode: 'transfer',
  aspectRatio: '9:16',
  quality: '480p',
  refImageSource: 'prev',
  motionVideoSource: 'prev',
  audioMode: 'original',
  audioPassthrough: true,
  deliveryPreset: 'tiktok',
  fps60: true,
  loraRelight: 0,
  clipStrength: 1.2,
  faceStrength: 0.6,
  faceSource: 'ref',
  poseStrength: 0.8,
  skipFirstFrames: 0,
  matchRef: false,
  matchRefMethod: 'reinhard',
  matchRefStrength: 0,
  brightCap: 1.0,
  motionSpeedup: 0,
}

function normalizeMotionSegmentConfig(type, config = {}) {
  if (type !== 'motion') return config
  const out = { ...config }
  const selectedFaceSource = ['driver', 'ref'].includes(String(out.faceSource || out.face_source || '').toLowerCase())
    ? String(out.faceSource || out.face_source).toLowerCase()
    : ''
  // ALD 30/06/2026 - GIỮ lựa chọn chất lượng 720p/540p của user (custom toggle) — KHÔNG để MULTI_OUTFIT_MOTION_CONFIG đè '480p'.
  const selectedQuality = ['720p', '540p'].includes(String(out.quality || '')) ? String(out.quality) : ''
  // ALD 28/06/2026 - BG Anchor/Khoá nền removed: Wan baseline chỉ nhận image ref + video motion.
  delete out.bgAnchor
  delete out.bg_anchor
  delete out.bgAnchorMaskExpand
  delete out.bg_anchor_mask_expand
  delete out.bgAnchorMaskBlur
  delete out.bg_anchor_mask_blur
  delete out.motionHandFix
  delete out.motion_hand_fix
  delete out.hand_fix_steps
  delete out.hand_fix_pose_strength
  delete out.hand_fix_clip_strength
  const start = Number(out.driverStartSec ?? out.driver_start_sec ?? 0)
  const dur = Number(out.driverDurSec ?? out.driver_dur_sec ?? 0)
  const isFiveSecSegment = Number.isFinite(start) && Number.isFinite(dur) && dur > 0 && start % 5 === 0
  const isMultiOutfit = out._gen === 'multi-outfit' || isFiveSecSegment
  if (isMultiOutfit) {
    Object.assign(out, MULTI_OUTFIT_MOTION_CONFIG, {
      driverStartSec: Number.isFinite(start) ? start : 0,
      driverDurSec: Number.isFinite(dur) && dur > 0 ? dur : 5,
      _gen: out._gen,
      _seg: out._seg,
    })
    if (selectedFaceSource) out.faceSource = selectedFaceSource
    if (selectedQuality) out.quality = selectedQuality   // ALD 30/06/2026 - giữ 720p/540p user chọn, không để MULTI_OUTFIT đè 480p
    // ALD 01/07/2026 - GIỮ tinh chỉnh TAY của user (poseStrength + mô tả tăng cường), không để preset multi-outfit đè.
    if (Number.isFinite(Number(config.poseStrength))) out.poseStrength = Number(config.poseStrength)
    if (String(config.extraPositive || '').trim()) out.extraPositive = String(config.extraPositive)
    delete out.cfg
    delete out.shift
    delete out.scheduler
  }
  if (!Number.isFinite(start) || !Number.isFinite(dur) || dur <= 0) return out
  // ALD 28/06/2026 - Legacy multi-outfit lưu driverDurSec = endSec (node2 5/10 = segment 5..10s); worker cần duration=5.
  if (start > 0 && Math.abs(dur - (start + 5)) < 0.001) {
    out.driverDurSec = 5
    out.driver_dur_sec = 5
  }
  // ALD 30/06/2026 - FIX: preset KHỚP đúng độ dài segment. Trước đây hardcode '5s-720p' (81f) → node tách driverDurSec=10
  // vẫn ra 5s. Giờ map theo số giây: 5→5s-720p · 10→10s-720p · 15→15s-720p · 20→20s-720p. Khuyến nghị tách ĐOẠN 10s
  // (10s-720p, 161f chạy THẲNG VRAM, nhanh); >15s mới offload RAM. Worker cũng có guard driverDurSec→frames.
  const segDur = Number(out.driverDurSec ?? out.driver_dur_sec ?? dur)
  if (segDur > 0 && start % 5 === 0) {
    const TABLE = [[5, '5s-720p', 81], [10, '10s-720p', 161], [15, '15s-720p', 241], [20, '20s-720p', 321], [30, '30s-720p', 481]]  // ALD 08/07 - +30s
    const [, preset, frames] = TABLE.reduce((b, c) => (Math.abs(c[0] - segDur) < Math.abs(b[0] - segDur) ? c : b))
    out.preset = preset
    out.frames = frames
    out.steps = Math.max(Number(out.steps || 4), 6)
    out.skipFirstFrames = 0
    out.skip_first_frames = 0
  }
  return out
}

function buildSingerMotionDefinition() {
  const N = 3
  const seg = 5
  const COL = 320, ROW = 200, X0 = 80, Y0 = 80
  const nodes = [], edges = [], tails = []
  const drvId = 'singer-driver'
  nodes.push({ id: drvId, type: 'input', position: { x: X0, y: Y0 }, data: { config: { contentType: 'video', source: 'session', field: 'driver_video', label: 'Video motion gốc', staticData: '', staticMime: '', staticName: '', _gen: 'multi-outfit' } } })
  for (let i = 1; i <= N; i++) {
    const y = Y0 + i * ROW
    const refId = `singer-ref-${i}`
    const tryonId = `singer-tryon-${i}`
    const motionId = `singer-motion-${i}`
    nodes.push({ id: refId, type: 'input', position: { x: X0, y }, data: { config: { contentType: 'image', source: 'session', field: `outfit_${i}`, label: `Outfit ${i}`, staticData: '', staticMime: '', staticName: '', _gen: 'multi-outfit', _seg: i } } })
    nodes.push({ id: tryonId, type: 'tryon', position: { x: X0 + COL, y }, data: { config: { ...defaultConfig('tryon'), cleanOnly: true, productCount: 1, prompt: 'Clean and refine this model/outfit image before motion transfer. Keep the same person, outfit, face, pose, and full-body framing. Remove artifacts, improve lighting, keep a realistic fashion look.', _gen: 'multi-outfit', _seg: i } } })
    nodes.push({ id: motionId, type: 'motion', position: { x: X0 + COL * 2, y }, data: { config: { ...defaultConfig('motion'), ...MULTI_OUTFIT_MOTION_CONFIG, skipFirstFrames: 0, driverStartSec: (i - 1) * seg, driverDurSec: seg, _gen: 'multi-outfit', _seg: i } } })
    edges.push({ id: `singer-e-ref-${i}`, source: refId, target: tryonId, targetHandle: 'model' })
    edges.push({ id: `singer-e-clean-${i}`, source: tryonId, target: motionId, targetHandle: 'image' })
    edges.push({ id: `singer-e-driver-${i}`, source: drvId, target: motionId, targetHandle: 'motion' })
    tails.push(motionId)
  }
  const midY = Y0 + (ROW * (N + 1)) / 2
  const cnId = 'singer-concat'
  nodes.push({ id: cnId, type: 'concat', position: { x: X0 + COL * 3, y: midY }, data: { config: { ...defaultConfig('concat'), clipCount: N, transition: 'cut', transitionDuration: 0, fps: 0, audioMode: 'source', _gen: 'multi-outfit' } } })
  tails.forEach((id, idx) => edges.push({ id: `singer-e-concat-${idx + 1}`, source: id, target: cnId, targetHandle: `clip${idx + 1}` }))
  edges.push({ id: 'singer-e-concat-audio', source: drvId, target: cnId, targetHandle: 'audio' })
  nodes.push({ id: 'singer-output', type: 'output', position: { x: X0 + COL * 4, y: midY }, data: { config: { format: 'video', _gen: 'multi-outfit' } } })
  edges.push({ id: 'singer-e-output', source: cnId, target: 'singer-output' })
  return { nodes, edges }
}

// ALD 08/07/2026 - Mẫu SẴN "Thử đồ → Đè lộ" (dễ test): 1 mẫu + 2 bộ đồ + 1 video motion → tryon×2 → motion×2 (preset
// 5s test, CÙNG driver → động tác khớp) → node Đè lộ (motionA=Nền, motionB=Đồ mới). Dựng thẳng lên canvas, chỉ cần
// upload 4 input rồi Chạy. Node gắn _gen:'tryon-reveal' để dựng lại biết mà xoá.
function buildTryonRevealNodes() {
  const COL = 300
  const nodes = [], edges = []
  const mk = (id, type, x, y, config) => nodes.push({ id, type: 'step', position: { x, y }, data: { type, config: { ...config, _gen: 'tryon-reveal' } } })
  const e = (id, source, target, targetHandle) => edges.push({ id, source, target, ...(targetHandle ? { targetHandle } : {}), type: 'step' })
  const inCfg = (field, label) => ({ contentType: field === 'driver_video' ? 'video' : 'image', source: 'session', field, label, staticData: '', staticMime: '', staticName: '' })

  mk('tr-prodA', 'input', 60, 40, inCfg('product_a', 'Đồ A'))
  mk('tr-model', 'input', 60, 250, inCfg('model_image', 'Người mẫu'))
  mk('tr-prodB', 'input', 60, 480, inCfg('product_b', 'Đồ B'))
  mk('tr-driver', 'input', 60, 690, inCfg('driver_video', 'Video motion'))

  mk('tr-tryonA', 'tryon', 60 + COL, 90, { ...defaultConfig('tryon'), garmentType: 'auto' })
  mk('tr-tryonB', 'tryon', 60 + COL, 470, { ...defaultConfig('tryon'), garmentType: 'auto' })
  e('tr-e-mA', 'tr-model', 'tr-tryonA', 'model'); e('tr-e-pA', 'tr-prodA', 'tr-tryonA', 'product')
  e('tr-e-mB', 'tr-model', 'tr-tryonB', 'model'); e('tr-e-pB', 'tr-prodB', 'tr-tryonB', 'product')

  mk('tr-motionA', 'motion', 60 + COL * 2, 90, { ...defaultConfig('motion'), preset: '5s-720p' })
  mk('tr-motionB', 'motion', 60 + COL * 2, 470, { ...defaultConfig('motion'), preset: '5s-720p' })
  e('tr-e-tA', 'tr-tryonA', 'tr-motionA', 'image'); e('tr-e-dA', 'tr-driver', 'tr-motionA', 'motion')
  e('tr-e-tB', 'tr-tryonB', 'tr-motionB', 'image'); e('tr-e-dB', 'tr-driver', 'tr-motionB', 'motion')

  mk('tr-reveal', 'reveal', 60 + COL * 3, 280, { ...defaultConfig('reveal') })
  e('tr-e-rBase', 'tr-motionA', 'tr-reveal', 'base'); e('tr-e-rRev', 'tr-motionB', 'tr-reveal', 'reveal')

  mk('tr-output', 'output', 60 + COL * 4, 280, { format: 'video' })
  e('tr-e-out', 'tr-reveal', 'tr-output')
  return { nodes, edges }
}

async function insertTryonRevealTemplate() {
  if (!isOwned.value || isViewingHistory.value) return
  const existing = nodes.value.filter((n) => n.data?.config?._gen === 'tryon-reveal')
  if (existing.length) {
    const ok = await confirmDialog.ask({ title: 'Dựng lại mẫu?', message: 'Canvas đã có mẫu Thử đồ–Đè lộ. Dựng lại sẽ XOÁ mẫu cũ rồi tạo mới.', confirmText: 'Dựng lại', cancelText: 'Huỷ', variant: 'danger' })
    if (!ok) return
    const gid = new Set(existing.map((n) => n.id))
    nodes.value = nodes.value.filter((n) => !gid.has(n.id))
    edges.value = edges.value.filter((e) => !gid.has(e.source) && !gid.has(e.target))
  }
  const { nodes: nn, edges: ee } = buildTryonRevealNodes()
  addNodes(nn); addEdges(ee)
  await nextTick()
  fitView({ padding: 0.2, duration: 250 })
  selectedNodeId.value = 'tr-reveal'
  toast.success('Đã dựng mẫu "Thử đồ → Đè lộ". Upload: Người mẫu + Đồ A + Đồ B + Video motion, rồi bấm Chạy.')
}

function needsSingerRepair(wfData) {
  if (!wfData || String(wfData.slug || '').toLowerCase() !== 'singer') return false
  const nodes = Array.isArray(wfData.definition?.nodes) ? wfData.definition.nodes : []
  const hasCleanTryon = nodes.filter((n) => n?.type === 'tryon' && n?.data?.config?.cleanOnly).length >= 3
  const motions = nodes.filter((n) => n?.type === 'motion')
  const hasThreeFiveSecMotions = motions.length >= 3 && motions.slice(0, 3).every((n, i) =>
    n?.data?.config?.preset === '5s-720p' &&
    Number(n?.data?.config?.driverStartSec) === i * 5 &&
    Number(n?.data?.config?.driverDurSec) === 5)
  return !hasCleanTryon || !hasThreeFiveSecMotions
}

// ALD 30/06/2026 - Dựng workflow từ kịch bản: gửi kịch bản lên BE (POST /workflows/generate-from-script),
// AI Qwen phân tích + tự gọi tool dựng node → trả { nodes, edges } (canvas-shape) → addNodes/addEdges thẳng.
// BE tự nhả model khỏi VRAM sau khi xong. Node tự sinh gắn _gen:'script' để lần sau dựng lại biết mà xoá.
// ── Dựng workflow từ payload (dùng chung cho chat đạo diễn) ──
async function doBuildWorkflow(payload) {
  if (!isOwned.value || isViewingHistory.value) return { ok: false, error: 'không có quyền chỉnh' }
  const script = String(payload?.script || '').trim()
  const storyboard = payload?.storyboard || null   // ALD 07/07 - ảnh storyboard/nhân vật: BE dùng Qwen-VL suy kịch bản
  if (!script && !storyboard) return { ok: false, error: 'thiếu kịch bản hoặc ảnh storyboard' }
  const existing = nodes.value.filter((n) => ['multi-outfit', 'script'].includes(n.data?.config?._gen))
  if (existing.length) {
    const ok = await confirmDialog.ask({
      title: 'Dựng lại từ kịch bản?',
      message: 'Canvas đã có node tự sinh. Dựng lại sẽ XOÁ các node tự sinh cũ rồi tạo mới.',
      confirmText: 'Dựng lại', cancelText: 'Huỷ', variant: 'danger',
    })
    if (!ok) return { ok: false, cancelled: true }
    const gid = new Set(existing.map((n) => n.id))
    nodes.value = nodes.value.filter((n) => !gid.has(n.id))
    edges.value = edges.value.filter((e) => !gid.has(e.source) && !gid.has(e.target))
  }
  try {
    const auth = useAuth()
    const res = await auth.beFetch('/workflows/generate-from-script', {
      method: 'POST',
      body: {
        script,
        aspectRatio: payload.aspectRatio || moGen.aspectRatio,
        maxScenes: Math.max(1, Math.min(10, Number(payload.maxScenes || moGen.maxScenes) || 6)),
        think: !!moGen.think,
        answers: payload.answers || null,
        product: payload.product || null,
        model: payload.model || null,
        modelStrategy: payload.modelStrategy || 'library',
        voice: payload.voice || '',                 // ALD 03/07/2026 - giọng user chọn trong chat
        subtitle: payload.subtitle === true,        // phụ đề opt-in (mặc định KHÔNG chèn)
        storyboard,                                 // ALD 07/07 - ảnh storyboard/nhân vật (Qwen-VL đọc → suy kịch bản)
      },
      timeout: 300000,
    })
    // ALD 07/07 - BE cần ảnh nhân vật gốc để giữ nhất quán → nhả tín hiệu cho chat nhắc user upload.
    if (res?.needImage) return { ok: false, needImage: res.needImage, message: res.message || '', scenes: res.scenes || [], warning: '' }
    const newNodes = Array.isArray(res?.nodes) ? res.nodes : []
    const newEdges = Array.isArray(res?.edges) ? res.edges : []
    if (!newNodes.length) return { ok: false, error: 'AI không dựng được node nào' }
    addNodes(newNodes)
    addEdges(newEdges)
    await nextTick()
    fitView({ padding: 0.2, duration: 250 })
    const out = newNodes.find((n) => n.data?.type === 'output')
    selectedNodeId.value = out?.id || newNodes[newNodes.length - 1]?.id || null
    return { ok: true, count: newNodes.length, warning: res?.warning || '' }
  } catch (e) {
    return { ok: false, error: e?.data?.error || e?.message || 'Lỗi không xác định' }
  }
}

// ── Chat đạo diễn: hội thoại hỏi-đáp (script → sản phẩm → người mẫu → phỏng vấn → dựng) ──
function moFmt(text) {
  return String(text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\n/g, '<br>')
}
async function moScrollBottom() { await nextTick(); const el = moChatScroll.value; if (el) el.scrollTop = el.scrollHeight }
function moPush(role, msg) { moGen.chat.push({ role, ...msg }); moScrollBottom() }
function moAskAI(step) { moGen.pending = step; moGen.draft = ''; moGen.otherActive = false; moPush('ai', { text: step.prompt }) }

const MO_MODEL_OPTS = [
  { value: 'own', label: '📤 Tôi cung cấp ảnh mẫu' },
  { value: 'library', label: '👥 Tự chọn từ kho' },
  { value: 'generate', label: '✨ AI sinh diễn viên mới' },
]
// ALD 03/07/2026 - chọn giọng trong chat đạo diễn: mặc định viXTTS + toàn bộ giọng CLONE từ Thư viện giọng
// (useVoices → value 'voicelib:<id>', worker _tts route theo tiền tố). Load khi mở modal.
const moVoicesLib = useVoices()
const moVoiceOpts = computed(() => [
  { value: '', label: '🔊 Giọng mặc định (viXTTS)' },
  ...moVoicesLib.options.value.map((o) => ({ value: o.value, label: `🎙️ ${o.label}` })),
])
const MO_SUB_OPTS = [
  { value: false, label: '🚫 Không cần phụ đề' },
  { value: true, label: '💬 Có, chèn phụ đề vào video' },
]
// ALD 03/07/2026 - kịch bản mẫu "quảng cáo CÔNG DỤNG sản phẩm": input = ảnh người mẫu + ảnh sản phẩm;
// mẫu CẦM sản phẩm → đạo diễn dựng keyframe bằng node Sửa ảnh (ghép), KHÔNG dùng Đặt sản phẩm (đã bỏ).
const MO_SAMPLE_SCRIPT = `Quảng cáo công dụng sản phẩm (~20 giây, 3 cảnh, người thật, dọc 9:16):
Cảnh 1 — Người mẫu cầm sản phẩm trên tay, mỉm cười nhìn thẳng camera giới thiệu: "Da xỉn màu, thiếu sức sống? Đây chính là bí quyết của mình."
Cảnh 2 — Cận cảnh người mẫu dùng thử sản phẩm: thoa nhẹ lên mặt, biểu cảm thư giãn, làn da căng bóng rạng rỡ. Lời thuyết minh: "Thẩm thấu nhanh, cấp ẩm tức thì — chỉ sau 7 ngày da căng mịn, sáng khỏe rõ rệt."
Cảnh 3 — Người mẫu cầm sản phẩm bên gương mặt, nhìn thẳng camera kêu gọi: "Inbox ngay cho shop để được tư vấn và nhận ưu đãi hôm nay nhé!"`

function moChatStart() {
  if (!isOwned.value || isViewingHistory.value) { toast.error('Mở workflow của bạn để dùng'); return }
  moGen.chat = []
  moGen.collected = { answers: {} }
  moGen.queue = []
  moGen.pending = null
  moGen.draft = ''
  moGen.otherActive = false
  moGen.loading = false
  moGen.busy = false
  moPush('ai', { text: 'Chào bạn 👋 Mình là **đạo diễn AI**. Tải lên **ảnh storyboard / cảnh mẫu / nhân vật** để mình nhìn và dàn cảnh — hoặc **bỏ qua** rồi gõ **kịch bản** chữ. Mình sẽ hỏi thêm vài câu rồi tự dựng phim người thật.' })
  moAskAI({ key: 'storyboard', kind: 'asset', allowSkip: true, prompt: 'Bạn có **ảnh storyboard / cảnh mẫu / nhân vật gốc** không? Tải lên (hoặc dán URL) để mình phân tích và dàn cảnh. **Bỏ qua** nếu muốn tự gõ kịch bản.' })
}

async function moNext() {
  const c = moGen.collected
  // ALD 07/07 - ưu tiên hỏi ẢNH storyboard/nhân vật (đạo diễn tự đọc → suy kịch bản); có ảnh thì KHÔNG bắt gõ kịch bản.
  if (c.storyboard === undefined) return moAskAI({ key: 'storyboard', kind: 'asset', allowSkip: true, prompt: 'Bạn có **ảnh storyboard / nhân vật gốc** không? Tải lên (hoặc dán URL). **Bỏ qua** để tự gõ kịch bản.' })
  if (!c.script && !c.storyboard) return moAskAI({ key: 'script', kind: 'textarea', prompt: 'Dán kịch bản / ý tưởng của bạn nhé:' })
  if (c.product === undefined) return moAskAI({ key: 'product', kind: 'asset', allowSkip: true, prompt: 'Bạn có **ảnh sản phẩm** thật không? Tải lên hoặc dán URL để giữ đúng bao bì/nhãn. (Bỏ qua nếu chưa có)' })
  if (!c.modelStrategy) return moAskAI({ key: 'model', kind: 'options', options: MO_MODEL_OPTS, prompt: '**Người mẫu** trong phim lấy từ đâu?' })
  if (c.modelStrategy === 'own' && c.model === undefined) return moAskAI({ key: 'modelAsset', kind: 'asset', allowSkip: true, prompt: 'Tải **ảnh người mẫu** của bạn lên (hoặc dán URL). Bỏ qua thì mình lấy từ kho.' })
  // ALD 03/07/2026 - user chọn GIỌNG (kèm giọng clone từ Thư viện giọng) + PHỤ ĐỀ opt-in (mặc định KHÔNG).
  if (c.voice === undefined) return moAskAI({ key: 'voice', kind: 'options', options: moVoiceOpts.value, prompt: 'Chọn **giọng đọc / lồng tiếng** cho phim (giọng clone bạn đã tải trong Thư viện giọng cũng ở đây):' })
  if (c.subtitle === undefined) return moAskAI({ key: 'subtitle', kind: 'options', options: MO_SUB_OPTS, prompt: 'Có muốn **chèn phụ đề** vào video không?' })
  if (c.script && !c._ivDone) return moFetchInterviewChat()   // ALD 07/07 - storyboard-only (không gõ kịch bản) → bỏ phỏng vấn, dựng luôn
  if (moGen.queue.length) return moAskNextIv()
  return moBuildChat()
}

function moAskNextIv() {
  const q = moGen.queue.shift()
  if (!q) return moNext()
  moAskAI({ key: 'iv', qid: q.id, kind: 'options', allowOther: q.allowOther !== false, options: (q.options || []).map((o) => ({ value: o.value, label: o.label })), prompt: q.question })
}

async function moFetchInterviewChat() {
  const c = moGen.collected
  c._ivDone = true
  moGen.pending = null
  moGen.busy = true
  try {
    const auth = useAuth()
    const res = await auth.beFetch('/workflows/interview-script', {
      method: 'POST',
      body: { script: c.script, think: false, hasProductImage: !!c.product, hasModelImage: c.modelStrategy === 'own' && !!c.model },
      timeout: 180000,
    })
    moGen.queue = Array.isArray(res?.questions) ? res.questions : []
  } catch {
    moGen.queue = []
  } finally {
    moGen.busy = false
    if (moGen.queue.length) moAskNextIv()
    else moBuildChat()
  }
}

function moRecord(p, value) {
  const c = moGen.collected
  if (p.key === 'script') c.script = value
  else if (p.key === 'storyboard') c.storyboard = value || null   // ALD 07/07 - ảnh storyboard/nhân vật (null nếu Bỏ qua)
  else if (p.key === 'needModel') { c.model = value; c.modelStrategy = 'own' }  // upload ảnh nhân vật gốc khi BE báo thiếu
  else if (p.key === 'product') c.product = value
  else if (p.key === 'model') c.modelStrategy = value
  else if (p.key === 'modelAsset') c.model = value
  else if (p.key === 'voice') c.voice = value             // ALD 03/07/2026 - '' = giọng mặc định viXTTS
  else if (p.key === 'subtitle') c.subtitle = value       // true/false — phụ đề opt-in
  else if (p.key === 'iv' && p.qid) c.answers[p.qid] = value
  moGen.pending = null
}
function moPick(opt) {
  const p = moGen.pending; if (!p) return
  moPush('user', { text: opt.label })
  moRecord(p, opt.value)
  moNext()
}
function moSendText() {
  const p = moGen.pending; const v = String(moGen.draft || '').trim()
  if (!p || !v) return
  moPush('user', { text: v })
  moRecord(p, v)
  moGen.draft = ''; moGen.otherActive = false
  moNext()
}
function moSendUrl() {
  const p = moGen.pending; const v = String(moGen.draft || '').trim()
  if (!p) return
  if (!/^https?:\/\//i.test(v)) { toast.error('URL ảnh không hợp lệ'); return }
  moPush('user', { text: v, image: v })
  moRecord(p, { kind: 'url', url: v })
  moGen.draft = ''
  moNext()
}
function moSkip() {
  const p = moGen.pending; if (!p) return
  moPush('user', { text: 'Bỏ qua / điền sau' })
  moRecord(p, null)
  moNext()
}
async function moUpload(ev) {
  const file = ev.target?.files?.[0]
  if (!file) return
  const p = moGen.pending
  moGen.uploadingChat = true
  try {
    const res = await moStorageFiles.uploadFile(file, { bucket: 'chat-attachments', prefix: 'wf-asset' })
    if (res?.signedUrl && res?.path) {
      const asset = { kind: 'upload', url: res.signedUrl, path: res.path, bucket: res.bucket || 'chat-attachments', name: file.name, mime: file.type }
      moPush('user', { text: file.name, image: res.signedUrl })
      moRecord(p, asset)
      moNext()
    }
  } catch (e) {
    toast.error('Upload ảnh lỗi: ' + (e?.data?.error || e?.message || ''))
  } finally {
    moGen.uploadingChat = false
    if (ev.target) ev.target.value = ''
  }
}

async function moBuildChat() {
  const c = moGen.collected
  moGen.pending = { kind: 'building' }
  moPush('ai', { text: 'Tuyệt! Đủ thông tin rồi 🎬 Đang dựng phim người thật cho bạn… (có thể mất 1-2 phút, giữ cửa sổ mở nhé)' })
  moGen.loading = true
  const r = await doBuildWorkflow({
    script: c.script,
    storyboard: c.storyboard || null,   // ALD 07/07 - ảnh storyboard/nhân vật → BE Qwen-VL suy kịch bản
    answers: c.answers,
    product: c.product || null,
    model: c.modelStrategy === 'own' ? (c.model || null) : null,
    modelStrategy: c.modelStrategy || 'library',
    // ALD 03/07/2026 - giọng user chọn ('' = viXTTS mặc định) + phụ đề opt-in (mặc định false).
    voice: c.voice || '',
    subtitle: c.subtitle === true,
  })
  moGen.loading = false
  // ALD 07/07 - BE cần ảnh nhân vật gốc (giữ nhất quán) → nhắc user upload rồi dựng lại (moNext sẽ tự gọi moBuildChat).
  if (r.needImage) {
    if (Array.isArray(r.scenes) && r.scenes.length) {
      moPush('ai', { text: `🎬 Mình đọc được **${r.scenes.length} cảnh**:\n${r.scenes.map((s, i) => `${i + 1}. ${s.title || s.setting || ''}`).join('\n')}` })
    }
    moPush('ai', { text: `⚠️ ${r.message || 'Cần 1 ảnh nhân vật gốc để giữ nhất quán mọi cảnh.'}` })
    return moAskAI({ key: 'needModel', kind: 'asset', prompt: 'Tải **ảnh nhân vật gốc** (người/bộ áo dài chính) để mình giữ đúng người ở mọi cảnh:' })
  }
  moGen.pending = { kind: 'done' }
  if (r.ok) {
    // ALD 04/07/2026 - warning từ BE (vd vision không đọc được ảnh sản phẩm) phải NỔI LÊN cho user thấy,
    // không chôn trong log (sự cố "giày ra phim nước hoa").
    moPush('ai', { text: `✅ Xong! Mình đã dựng **${r.count} node** lên canvas, đã nạp sẵn ảnh & thông tin bạn cung cấp. Bạn kiểm tra rồi bấm **Lưu** nhé.${r.warning ? `\n\n${r.warning}` : ''}` })
    setTimeout(() => { if (moGenOpen.value && !moGen.loading) moGenOpen.value = false }, 2200)
  } else if (r.cancelled) {
    moPush('ai', { text: 'Đã huỷ. Bạn có thể đóng cửa sổ hoặc bấm Làm lại.' })
  } else {
    moPush('ai', { text: `❌ Dựng lỗi: ${r.error}. Bạn thử **Làm lại** hoặc chỉnh kịch bản nhé.` })
  }
}
// #endregion

function defaultConfig(type) {
  switch (type) {
    case 'input':     return { contentType: 'text', source: 'session', field: 'text' }
    case 'output':    return { format: 'markdown', cleanup: false }
    // ALD 11/06/2026 - provider HuggingFace ĐÃ GỠ theo yêu cầu user (backend còn nhánh ngủ đông).
    case 'motion':    return { preset: 'drv-15s', mode: 'transfer', renderProfile: 'fast', refImageSource: 'prev', motionVideoSource: 'prev', audioMode: 'original', audioPassthrough: true, deliveryPreset: 'tiktok', fps60: true, loraRelight: 0, skipFirstFrames: 0, matchRef: false, matchRefMethod: 'reinhard', matchRefStrength: 0, warmth: 0, brightCap: 1.0, faceStrength: 0.6, faceSource: 'driver', poseStrength: 0.8, clipStrength: 1.2, extraPositive: 'soft even matte lighting with retained detail in bright areas, natural matte skin with visible pores and realistic texture, stable natural mouth and lips, steady well-formed bare hands with five clearly separated fingers, natural fingertips, clean short natural fingernails' }
    case 'tryon':     return { provider: 'qwen', garmentType: 'upper', autoAnalyze: true, brightness: 0, outputRes: '' }
    case 'create-image': return { provider: 'qwen', model: 'qwen-edit', geminiModel: 'nano-banana-pro', prompt: '', promptMode: 'text', promptJson: '', negativePrompt: '', apiKey: '', inputCount: 0, useModelStandard: false, modelStandardPreset: 'female', modelStandardPrompt: '', refineSteps: 0, realismPreset: 'real_photo', outputCount: 1, aspectRatio: 'auto', quality: 'standard' }
    case 'edit-image': return { provider: 'qwen', model: 'qwen-edit', geminiModel: 'nano-banana-pro', prompt: '', negativePrompt: '', apiKey: '', inputCount: 1, outputCount: 1, quality: '1080' }
    case 'compose':   return { provider: 'qwen', personCount: 1, keepFace: true, subjectKind: 'person', sceneNote: '', prompt: '', autoPrompt: true }
    case 'product-overlay': return { mode: 'natural-hold', productPlacement: 'auto', position: 'bottom-right', scale: 0.28, padding: 0.035, card: false, label: '', prompt: '', negativePrompt: '' }
    // ALD 30/06/2026 - cast-model: tuyển người mẫu từ kho (model_refs) theo giới tính/độ tuổi khi chỉ có ảnh sản phẩm.
    case 'cast-model': return { gender: 'female', ageGroup: 'young', seed: 0, label: 'Người mẫu (kho)' }
    // ALD 11/06/2026 - node khai báo key: providerType (gemini|veo|custom) + apiKey (mask server-side).
    case 'api-key':   return { providerType: 'gemini', apiKey: '' }
    // ALD 14/06/2026 - text-to-video: CHỈ prompt (không ảnh) → video ngắn. model dropdown.
    // ALD 03/07/2026 - mặc định wan2.2 (MoE + LoRA distill 4 bước lightx2v — nhanh, cfg 1.0, đỡ cháy sáng).
    case 'text-to-video': return { model: 'wan2.2', duration: 5, aspectRatio: '16:9', prompt: '', negativePrompt: '' }
    case 'teen-flycam': return { preset: '', duration: 10, shotCount: 5, aspectRatio: '9:16', wanModel: 'wan2.2', modelGender: 'auto', driverMode: 'custom', driverUrls: '', steps: 20, seedMode: 'random', audioMode: 'preset' }
    case 'trend-tiktok': return { preset: 'paper-rip', duration: 6, shotCount: 2, aspectRatio: '9:16', wanModel: 'wan2.2', modelGender: 'auto', steps: 20, seedMode: 'random', audioMode: 'preset' }
    // ALD 14/06/2026 - ss: Ảnh→Video LTX-2.3 + LoRA custom. loraName khớp model_files.filename (type loras).
    case 'ss': return { model: 'ltx', prompt: '', negativePrompt: '', promptMode: 'text', promptJson: '', duration: 5, aspectRatio: '9:16', linkMode: 'anchor', loraName: '', loraStrength: 1.0, inputCount: 1 }
    // ALD 03/07/2026 - wan-i2v mặc định wan2.2 distill + matchRef (giữ màu ảnh gốc, chống cháy sáng).
    // endEnabled:false = ẩn cổng "Ảnh cuối" cho node mới; bật toggle trong Inspector mới hiện cổng (FLF).
    // ALD 10/07/2026 - provider 'self-host' | 'dashscope' (Alibaba happyhorse/wan2.x i2v cloud, key qua node API Key Type dashscope)
    case 'wan-i2v': return { prompt: '', negativePrompt: '', duration: 5, aspectRatio: '9:16', wanModel: 'wan2.2', matchRef: true, endEnabled: false, provider: 'self-host', dashscopeModel: 'happyhorse-1.0-i2v', dashscopeResolution: '720P', dashscopePromptExtend: true }
    case 'talk':      return { line: '', voice: 'gemini:Aoede', prompt: '', fps: 25 }
    case 'voiceover': return { script: '', voice: 'vixtts', mix: 'replace' }
    case 'concat':    return { clipCount: 2, transition: 'fade', transitionDuration: 0.35, softCutFrames: 3, fps: 0, audioMode: 'clips' }
    case 'reveal':    return { revealMode: 'slider', direction: 'down', customSlider: false, sweepAtSec: null, startPos: 0, endPos: 1, showLine: true, sweepDuration: 1, loop: false, bandPct: 0.25, vortexTwists: 2, swapBase: false }
    case 'subtitle':  return { mode: 'subtitle', targetLang: 'vi', bilingual: false, asrModel: 'medium', fontSize: 18, position: 'bottom', voice: '', voiceSpeed: 1.3 }
    case 'enhance':        return { targetRes: '1080p', fpsInterp: '60', faceRestore: true, faceFidelity: 0.5 }  // ALD 09/07 - CodeFormer mặt mặc định BẬT (mode ảnh)

    case 'workflow':  return { slug: '' }
    case 'condition': return { expression: 'text.length > 100' }
    case 'http':      return { method: 'POST', url: '', headers: {}, body: '', timeout: 30000 }
    case 'gpu-warmup':  return { wait_for_healthy: false, timeout_sec: 300, use_eta: true }
    case 'gpu-free':    return { max_wait_sec: 45, poll_interval_sec: 3, free_ollama: true, free_chandra: true, free_comfy: false, ollama_models: '' }
    case 'validate':    return { required_fields: [], math_checks: [], strict: false }
    case 'debug':       return { label: 'Debug step', captureImage: true, captureVideo: true, captureAudio: false, captureText: true }
    default:            return {}
  }
}

function onNodeClick({ node }) { selectedNodeId.value = node.id }

function onNodesChange(changes) {
  for (const c of changes) {
    if (c.type === 'remove' && c.id === selectedNodeId.value) selectedNodeId.value = null
  }
}

function onConnect(connection) {
  if (isViewingHistory.value) return // read-only khi xem history
  const sourceNode = nodes.value.find((n) => n.id === connection.source)
  let label = null
  let className = ''
  // Condition node: true/false branches
  if (sourceNode?.data?.type === 'condition') {
    label = connection.sourceHandle || 'true'
    className = label === 'true' ? 'edge-true' : 'edge-false'
    const dup = edges.value.find((e) => e.source === connection.source && e.sourceHandle === label)
    if (dup) {
      toast.error(`Nhánh "${label}" đã có edge.`)
      return
    }
  }
  // onError='route' node: success/error branches
  else if ((sourceNode?.data?.config?.onError) === 'route' && connection.sourceHandle) {
    label = connection.sourceHandle  // 'success' | 'error'
    className = label === 'error' ? 'edge-error' : 'edge-success'
    const dup = edges.value.find((e) => e.source === connection.source && e.sourceHandle === label)
    if (dup) {
      toast.error(`Nhánh "${label}" đã có edge.`)
      return
    }
  }
  addEdges([{
    id: `${connection.source}-${connection.target}-${Date.now().toString(36)}`,
    source: connection.source,
    target: connection.target,
    sourceHandle: connection.sourceHandle,
    targetHandle: connection.targetHandle,
    label,
    data: label ? { label } : undefined,
    class: className,
    type: 'step'
  }])
}

let _teenFlycamPresetSaveTimer = null
function scheduleTeenFlycamPresetSave() {
  if (isViewingHistory.value) return
  clearTimeout(_teenFlycamPresetSaveTimer)
  _teenFlycamPresetSaveTimer = setTimeout(() => {
    onSave()
  }, 450)
}

function updateNodeConfig(newConfig) {
  if (!selectedNode.value) return
  const prevConfig = selectedNode.value.data.config || {}
  const shouldAutoSaveTeenPreset =
    selectedNode.value.data?.type === 'teen-flycam' &&
    !isEqual(prevConfig.customPresets || [], newConfig?.customPresets || [])
  // Giữ field onError (common config) khi inspector update — inspector chỉ care type-specific keys.
  const prevOnError = selectedNode.value.data.config?.onError
  selectedNode.value.data = {
    ...selectedNode.value.data,
    config: { ...newConfig, ...(prevOnError ? { onError: prevOnError } : {}) }
  }
  dirty.value = true
  if (shouldAutoSaveTeenPreset) scheduleTeenFlycamPresetSave()
}

// #region ALD 07/07/2026 - Ghi config vào node THEO ID (không qua selectedNode).
// Inspector render bằng v-if="selectedNode" → đóng sidebar là unmount, watcher emit('update:config')
// bị huỷ → kết quả async (vd import social 'Lấy video' xong SAU khi đóng sidebar) rơi mất.
// Hàm này merge patch thẳng vào node theo id, sống độc lập với lifecycle inspector → provide xuống
// cho inspector gọi imperatively khi tác vụ async hoàn tất. Chỉ merge (không replace) nên an toàn.
function patchNodeConfigById(nodeId, patch) {
  if (!nodeId || !patch) return
  const n = nodes.value.find((x) => x.id === nodeId)
  if (!n) return
  n.data = { ...n.data, config: { ...(n.data.config || {}), ...patch } }
  dirty.value = true
}
provide('patchNodeConfigById', patchNodeConfigById)
// #endregion

function canHaveErrorRoute(type) {
  // input*, output, condition không có error branch (logic không phù hợp); api-key là node config (không chạy)
  return !['input', 'inputText', 'inputImage', 'inputFile', 'inputHistory', 'output', 'condition', 'api-key'].includes(type)
}

function onOnErrorChange(value) {
  if (!selectedNode.value) return
  selectedNode.value.data = {
    ...selectedNode.value.data,
    config: { ...(selectedNode.value.data.config || {}), onError: value }
  }
  // Khi switch từ 'route' về stop/continue, xoá edges sourceHandle='error' để dọn dangling
  if (value !== 'route') {
    edges.value = edges.value.filter((e) => !(e.source === selectedNode.value.id && e.sourceHandle === 'error'))
  }
  dirty.value = true
}

function onDeleteNode() {
  if (!selectedNode.value) return
  const id = selectedNode.value.id
  nodes.value = nodes.value.filter((n) => n.id !== id)
  edges.value = edges.value.filter((e) => e.source !== id && e.target !== id)
  selectedNodeId.value = null
}

async function onSave() {
  // Đang xem history (canvas đang là snapshot read-only) → KHÔNG lưu (tránh đè canvas sống).
  if (isViewingHistory.value) return
  if (!dirty.value || saving.value) return
  saving.value = true
  try {
    // ALD 27/05/2026 - Static source giờ upload thẳng lên bucket khi user pick file
    // (Inspector onFileSelected → staticUrl persistent). DB lưu URL nhẹ, không cần strip.
    // Vẫn strip staticData nếu có (fallback base64 từ workflow legacy hoặc upload fail)
    // để tránh bloat payload — nhưng count nhỏ + cảnh báo nhẹ hơn vì staticUrl đã giữ data.
    const definition = currentDefinition()
    const toSave = JSON.parse(JSON.stringify(definition))
    let strippedLegacy = 0
    for (const n of toSave.nodes || []) {
      const c = n.data?.config
      if (!c || typeof c !== 'object') continue
      if (c.staticData && c.staticData.length > 1024) {
        c.staticData = ''
        if (c.source === 'static' && !c.staticUrl) strippedLegacy++
      }
    }
    await wf.update(route.params.id, { definition: toSave })
    savedDefinition.value = toSave
    if (strippedLegacy > 0) {
      toast.warning(`${strippedLegacy} file static legacy chưa có staticUrl — re-upload để persist qua reload.`, { duration: 6000 })
    } else {
      toast.success('Đã lưu')
    }
  } catch (err) {
    toast.error(err.data?.error || err.message || 'Lưu thất bại')
  } finally {
    saving.value = false
    if (typeof document !== 'undefined' && document.activeElement instanceof HTMLElement) {
      document.activeElement.blur()
    }
  }
}

// Scan input nodes — nếu có node nào source=session, cần ask user; nếu không, run thẳng.
const sessionInputs = computed(() =>
  nodes.value
    .filter((n) => {
      const t = n.data?.type
      const isInputType = t === 'input' || t === 'inputText' || t === 'inputImage' || t === 'inputFile' || t === 'inputHistory'
      if (!isInputType) return false
      const source = n.data.config?.source || 'session'
      return source === 'session'
    })
    .map((n) => ({
      id: n.id,
      contentType: n.data.config?.contentType || ({ inputText: 'text', inputImage: 'image', inputFile: 'file', inputHistory: 'history' }[n.data.type] || 'text'),
      field: n.data.config?.field || (n.data.config?.contentType || 'text')
    }))
)

// Có job đang active polling (sống sót qua reload nhờ _live flag trong localStorage)
const hasActiveRun = computed(() => testHistory.value.some((r) => effectiveRunStatus(r) === 'running'))

// #region ALD 12/06/2026 - MULTI-RUN TAB (kiểu VS Code): backend đã chạy song song (WF_CONCURRENCY +
// WORKER_CONCURRENCY) → FE cho phép nhiều run cùng lúc trong 1 workflow. Mỗi run đang chạy TỰ có 1 tab
// phía trên canvas; run xong giữ tab tới khi user bấm ✕ (entry vẫn nằm trong history drawer). Tab
// "Soạn thảo" = về chế độ edit (selectedRunId=null). Poll nền đã sẵn: chỉ run đang chọn mới chiếu lên
// canvas (projection watcher key theo selectedRunId), các run khác poll ngầm.
const MAX_CONCURRENT_RUNS = 3
const runningCount = computed(() => testHistory.value.filter((r) => effectiveRunStatus(r) === 'running').length)
const openTabIds = ref([])
const runTabs = computed(() => {
  const ids = []
  for (const r of testHistory.value) if (effectiveRunStatus(r) === 'running' && !ids.includes(r.id)) ids.push(r.id)
  for (const id of openTabIds.value) if (!ids.includes(id)) ids.push(id)
  return ids.map((id) => testHistory.value.find((r) => r.id === id)).filter(Boolean)
})
function focusRunTab(id) {
  if (!openTabIds.value.includes(id)) openTabIds.value.push(id)
  selectedRunId.value = id
  selectedNodeId.value = null
}
// Về tab Workflow (trang soạn thảo): bỏ chọn run → projection watcher khôi phục canvas sống. Chuyển qua
// lại tự do kể cả khi run đang chạy nền (run vẫn poll bình thường).
function focusEditTab() {
  selectedRunId.value = null
  selectedNodeId.value = null
}
// ALD 12/06/2026 - "Phiên mới": về canvas SỐNG để soạn prompt mới. TUYỆT ĐỐI KHÔNG chạy job (tách hẳn khỏi
// nút "Chạy workflow"). Run đang chạy vẫn poll nền, tab của nó vẫn còn.
function newBlankSession() {
  _exitSnapshot()            // khôi phục canvas đang sửa nếu đang xem snapshot 1 run
  selectedRunId.value = null
  selectedNodeId.value = null
  toast.info('Phiên soạn thảo mới — chỉnh prompt rồi bấm "Chạy workflow" để chạy.', { duration: 3000 })
}
function closeRunTab(id) {
  openTabIds.value = openTabIds.value.filter((x) => x !== id)
  if (selectedRunId.value === id) focusEditTab()
}
function tabTime(r) {
  try { return new Date(r.ts).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
  catch { return String(r.id).slice(0, 6) }
}
// #endregion

// ALD 26/05/2026 - Pre-validate input nodes source=library trước khi submit run.
// Sau khi duplicate workflow, libraryId có thể rỗng / trỏ tới item của user gốc →
// engine throw lỗi sau khi job đã start. Validate FE → toast rõ node nào, user
// mở inspector pick lại không tốn 1 chu kỳ submit-fail.
function validateLibraryInputs() {
  const missing = nodes.value.filter((n) => {
    const t = n.data?.type
    const isInputType = t === 'input' || t === 'inputText' || t === 'inputImage' || t === 'inputFile' || t === 'inputHistory'
    if (!isInputType) return false
    const c = n.data.config || {}
    return c.source === 'library' && !c.libraryId
  })
  if (missing.length === 0) return true
  const names = missing.map((n) => `"${n.data.config?.label || n.data?.type || n.id}"`).join(', ')
  toast.error(`Input ${names} đang dùng Library source nhưng chưa chọn file. Mở inspector pick lại trước khi chạy.`, { duration: 6000 })
  return false
}

async function openTestRun() {
  // ALD 12/06/2026 - nút "Chạy workflow" LUÔN hiện. Nếu đang xem 1 run (canvas = snapshot read-only) thì về
  // trang Workflow + khôi phục canvas SỐNG trước khi chạy (tránh chạy nhầm definition của run đang xem).
  if (isViewingHistory.value) {
    _exitSnapshot()
    selectedRunId.value = null
    await nextTick()
  }
  // ALD 12/06/2026 - cho chạy SONG SONG nhiều run (multi-run tab); chỉ chặn khi vượt trần.
  if (runningCount.value >= MAX_CONCURRENT_RUNS) {
    toast.warning(`Đang có ${runningCount.value} run chạy song song (tối đa ${MAX_CONCURRENT_RUNS}). Đợi bớt hoặc Cancel rồi chạy thêm.`, { duration: 4000 })
    return
  }
  // Đang có run khác → HỎI trước khi bắn thêm job (feedback 12/06: "click kia chạy thêm cái job đó luôn").
  if (runningCount.value > 0) {
    const ok = await confirmDialog.ask({
      title: 'Chạy thêm run mới?',
      message: `Đang có ${runningCount.value} run chạy nền. Chạy thêm 1 run song song nữa?`,
      confirmText: 'Chạy thêm',
      cancelText: 'Huỷ',
      variant: 'primary',
    })
    if (!ok) return
  }
  if (!validateLibraryInputs()) return
  // ALD 24/05/2026 - Workflow không có session input → confirm trước khi run thay vì
  // chạy thẳng. Tránh accidental Enter trên focused CTA button kick job nặng.
  if (sessionInputs.value.length === 0) {
    // Motion Transfer là job GPU nặng → confirm trước khi chạy.
    const hasHeavyNode = nodes.value.some((n) => n.data?.type === 'motion')
    if (hasHeavyNode) {
      const ok = await confirmDialog.ask({
        title: 'Chạy workflow?',
        message: 'Workflow có node Motion Transfer — thường ~6–22 phút GPU. Xác nhận chạy?',
        confirmText: 'Chạy',
        cancelText: 'Huỷ',
        variant: 'primary',
      })
      if (!ok) return
    }
    doTestRun()
    return
  }
  testRunOpen.value = true
}

// Cancel: mark entry as error locally, dừng poll loop (server job vẫn finish
// nhưng FE không care nữa). Người dùng có thể chạy test mới.
// ALD 28/05/2026 - REAL cancel: gọi BE POST /workflows/runs/:id/cancel để cascade
// cancel xuống Motion Transfer job. Worker poll DB mỗi 2s detect →
// interrupt ComfyUI + free VRAM. Trước đây chỉ patch FE local entry → server không
// biết → Wan job vẫn chạy nốt 8-22 phút → user spam Cancel + Run mới → 3 Wan jobs
// concurrent → server load 297 + sshd starve.
async function cancelRun(entry) {
  if (!entry || effectiveRunStatus(entry) !== 'running') return
  const runId = entry._runId || entry.id
  // Mark UI ngay (optimistic) — BE call async sau
  patchEntry(entry.id, {
    status: 'error',
    error: 'Đang cancel...',
    _live: false,
    durationMs: Date.now() - entry.ts,
  })
  testRunning.value = false
  runningStartTs.value = 0
  try {
    const auth = useAuth()
    const res = await auth.beFetch(`/workflows/runs/${runId}/cancel`, { method: 'POST' })
    const total = res?.cascaded?.motion_jobs?.length || 0
    patchEntry(entry.id, {
      status: 'error',
      error: total > 0
        ? `Cancelled. ${total} Motion Transfer job dừng (worker sẽ interrupt trong 2s).`
        : 'Cancelled. Không tìm thấy GPU job link với run này.',
    })
    toast.success(`Đã cancel (${total} GPU job dừng theo)`, { duration: 4000 })
  } catch (e) {
    patchEntry(entry.id, {
      status: 'error',
      error: `Cancel fail: ${e?.data?.error || e?.message || e}. Vui lòng reload + check workflow_runs.`,
    })
    toast.error(`Cancel fail: ${e?.message || e}`, { duration: 6000 })
  }
}

// Apply run state vào nodes trên canvas từ events array.
// Priority: error > warn > success > idle (error không bị override).
function applyRunStateFromEvents(events) {
  for (const ev of events || []) {
    if (!ev.node_id) continue
    const node = nodes.value.find((n) => n.id === ev.node_id)
    if (!node) continue
    const cur = node.data._runState
    if (ev.level === 'error') node.data._runState = 'error'
    else if (ev.level === 'warn' && cur !== 'error') node.data._runState = 'warn'
    else if (ev.level === 'success' && cur !== 'error' && cur !== 'warn') node.data._runState = 'success'
    // ALD 27/05/2026 - extra.uploadedFor: engine emit URL với uploadedFor = source input
    // node ID khi upload base64 lên storage cho tryon/motion. Project URL lên
    // input node để Inspector dùng làm fallback preview (sau khi staticData strip).
    if (ev.extra?.uploadedFor && ev.extra?.previewUrl) {
      const srcNode = nodes.value.find((n) => n.id === ev.extra.uploadedFor)
      if (srcNode) {
        const kind = ev.extra.previewKind === 'video' ? 'video'
                   : ev.extra.previewKind === 'audio' ? 'audio' : 'image'
        srcNode.data = {
          ...srcNode.data,
          _runOutput: { ...(srcNode.data._runOutput || {}), [kind]: ev.extra.previewUrl }
        }
      }
    }
    // ALD 27/05/2026 - Engine đính kèm previewUrl trong event.extra khi node xong →
    // populate _runOutput để FlowNode render preview ngay tại intermediate node
    // (tryon hiển thị ảnh kết quả, Motion Transfer hiển thị video).
    if (ev.extra?.previewUrl && !ev.extra?.uploadedFor) {
      const kind = ev.extra.previewKind === 'video' ? 'video' : 'image'
      const nextOut = {
        ...(node.data._runOutput || {}),
        [kind]: ev.extra.previewUrl,
        metadata: ev.extra.outputMeta || node.data._runOutput?.metadata || {}
      }
      // ALD 01/07/2026 - edit-image: mỗi version xong emit 1 preview → TÍCH LUỸ vào images[] để node hiện
      // lưới thumbnail dần (xong ảnh nào hiện ảnh đó), thay vì chỉ thấy ảnh cuối.
      if (node.data.type === 'edit-image' && kind === 'image') {
        const prev = Array.isArray(nextOut.images) ? nextOut.images : []
        if (!prev.some((it) => (typeof it === 'string' ? it : it?.url) === ev.extra.previewUrl)) {
          nextOut.images = [...prev, { url: ev.extra.previewUrl, label: ev.extra.previewLabel || `Ảnh ${prev.length + 1}` }]
        }
      }
      node.data = { ...node.data, _runOutput: nextOut }
    }
  }
}

// Patch existing history entry by id (immutable replace)
function patchEntry(entryId, patch) {
  const idx = testHistory.value.findIndex((r) => r.id === entryId)
  if (idx < 0) return
  testHistory.value = [
    ...testHistory.value.slice(0, idx),
    { ...testHistory.value[idx], ...patch },
    ...testHistory.value.slice(idx + 1)
  ]
  persistTestHistory()
}

// Poll loop cho 1 run — lookup entry theo entryId, update events/output live.
// Có thể được gọi từ doTestRun (lần đầu) hoặc resume khi mount (đã có entry).
// ALD 18/06/2026 - Track entry đang có poll loop chạy → tránh 2 loop cùng poll 1 entry
// (resumeLivePolls lúc mount + click watcher khởi động lại khi loop cũ đã thoát/cross-tab).
const _activePolls = new Set()

async function pollRunUntilDone(entryId, runId, startTs) {
  if (_activePolls.has(entryId)) return   // đã có loop khác poll entry này rồi
  _activePolls.add(entryId)
  const POLL_INTERVAL = 1500
  // ALD 24/05/2026 - Bỏ cap 15 phút (Motion Transfer có thể mất 6-22p, marketing
  // pipeline 5-15p). Engine tự set workflow_runs.status='error' khi fail, FE chỉ
  // dừng poll khi: (a) user cancel entry, (b) status terminal. Tab đóng → poll dừng.
  let lastEventCount = -1
  // ALD 11/07/2026 - Chống busy-loop: trước đây getRun fail (vd fetch bị adblocker chặn = ERR_BLOCKED_BY_CLIENT,
  // hoặc mạng chớp) thì `continue` NGAY không sleep → vòng lặp quay cực nhanh, hammer fetch, tab treo "không
  // khôi phục được". Giờ luôn sleep trước khi retry, và sau MAX_FAIL lần fail liên tiếp thì DỪNG poll (entry
  // vẫn 'running' trong localStorage → lần mở lại page resumeLivePolls tự nối lại → recoverable, không kẹt CPU).
  let failStreak = 0
  const MAX_FAIL = 20   // ~30s (POLL_INTERVAL 1500ms) fail liên tục → buông, khỏi treo
  const sleep = () => new Promise((r) => setTimeout(r, POLL_INTERVAL))
  testRunning.value = true
  runningStartTs.value = startTs

  while (true) {
    // Check user đã cancel entry này chưa (status đổi sang error trong localStorage)
    const cur = testHistory.value.find((r) => r.id === entryId)
    if (!cur || effectiveRunStatus(cur) !== 'running') {
      testRunning.value = false
      runningStartTs.value = 0
      _activePolls.delete(entryId)
      return
    }
    let run
    try {
      run = await wf.getRun(runId)
      failStreak = 0
    } catch (err) {
      failStreak++
      console.warn(`[poll] getRun fail (${failStreak}/${MAX_FAIL}), retry next:`, err?.message)
      if (failStreak >= MAX_FAIL) {
        console.warn('[poll] quá nhiều lần fail liên tiếp → dừng poll, entry giữ running để mở lại tự nối', runId)
        testRunning.value = false
        runningStartTs.value = 0
        _activePolls.delete(entryId)
        return
      }
      await sleep()   // LUÔN nghỉ trước khi thử lại — không quay busy-loop
      continue
    }
    if (!run) { await sleep(); continue }
    const events = run.events || []
    if (events.length !== lastEventCount) {
      lastEventCount = events.length
      // ALD 28/05/2026 - Bug fix: chỉ apply state vào canvas KHI user đang xem ENTRY này.
      // Trước đây resumeLivePolls (chạy onMounted) gọi applyRunStateFromEvents bất kể
      // selectedRunId → mở page mà có job nền đang chạy → canvas tự fill state run đó
      // dù user chưa click history → trông như "data cũ persist". Giữ patchEntry để
      // testHistory entry vẫn update events mới, nhưng node mutation skip.
      if (selectedRunId.value === entryId) {
        applyRunStateFromEvents(events)
      }
      patchEntry(entryId, { events, durationMs: Date.now() - startTs })
    }
    const runMeta = run.output?.metadata || {}
    const liveAsyncJob = isLiveJobMeta(runMeta)
    if (liveAsyncJob) {
      patchEntry(entryId, {
        status: 'running',
        output: run.output,
        events,
        error: null,
        durationMs: Date.now() - startTs,
        _live: true,
        _runId: runId,
      })
      const outputNodeRaw = nodes.value.find((n) => n.data.type === 'output')
      pollPendingMotion(entryId, runMeta.job_id, outputNodeRaw?.id, { isResume: true })
      testRunning.value = false
      runningStartTs.value = 0
      _activePolls.delete(entryId)
      return
    }
    if (run.status === 'success' || run.status === 'error') {
      patchEntry(entryId, {
        status: run.status,
        output: run.output,
        events,
        error: run.error_msg || null,
        durationMs: Date.now() - startTs,
        _live: false,
        _runId: runId   // giữ lại cho debug
      })
      // ALD 24/05/2026 - Set _runOutput cho output node để FlowNode render preview video.
      // Nếu Motion Transfer output có pending=true → start polling.
      // ALD 28/05/2026 - Chỉ mutate canvas output node KHI user đang xem entry này. Trước
      // đây resumeLivePolls (background) override canvas dù selectedRunId khác → user
      // reload page thấy preview của job background tự fill vào canvas.
      if (run.status === 'success' && run.output) {
        const isViewing = selectedRunId.value === entryId
        const outputNode = isViewing ? nodes.value.find((n) => n.data.type === 'output') : null
        if (outputNode) {
          outputNode.data = { ...outputNode.data, _runOutput: run.output.metadata || {}, _runState: 'success' }
        }
        const meta = run.output.metadata || {}
        if (meta.pending && (!meta.kind || meta.kind === 'motion') && meta.job_id) {
          if (outputNode) outputNode.data = { ...outputNode.data, _runState: 'running' }
          // pollPendingMotion tự check selectedRunId trong applySnapshot — pass outputNode.id
          // chỉ khi đang viewing để guard rõ hơn. Nếu user click vào sau, watcher projection
          // sẽ re-populate từ entry.output (đã patch ở patchEntry trên).
          const outputNodeRaw = nodes.value.find((n) => n.data.type === 'output')
          pollPendingMotion(entryId, meta.job_id, outputNodeRaw?.id)
        }
      }
      if (run.status === 'error' && selectedRunId.value === entryId) toast.error(run.error_msg || 'Workflow lỗi', { duration: 5000 })
      testRunning.value = false
      runningStartTs.value = 0
      _activePolls.delete(entryId)
      return
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL))
  }
}

// ALD 13/07/2026 - Theo dõi Motion Transfer job async.
// 5s/lần, 30 phút deadline. Update output node._runOutput khi done.
// ALD 24/05/2026 - Synthetic progress: worker chỉ emit ở coarse milestone (0.10, 0.30, 0.95)
// nên giữa các mốc đó FE tự nội suy + xoay caption để UX không bị stuck "30%" 10 phút.
// Tổng ETA expected ~12 phút cho 30s-720p (sampler ~10 phút).
const MOTION_STAGES = [
  { p: 0.02, label: 'Khởi tạo môi trường…' },
  { p: 0.08, label: 'Tải input lên GPU server…' },
  { p: 0.12, label: 'Nạp Wan 2.2 Animate 14B lên VRAM…' },
  { p: 0.24, label: 'Phân tích pose từ video motion…' },
  { p: 0.42, label: 'Sampling chuyển động…' },
  { p: 0.68, label: 'Tinh chỉnh chi tiết video…' },
  { p: 0.82, label: 'VAE decode frames…' },
  { p: 0.86, label: 'Composing video + audio passthrough…' },
  { p: 0.92, label: 'Replace audio (ffmpeg merge)…' },
  { p: 0.96, label: 'Upload output lên storage…' },
  { p: 0.99, label: 'Sắp xong, đợi worker mark done…' }
]
function motionStageFor(elapsed, totalMs = 12 * 60 * 1000) {
  const r = Math.min(0.99, elapsed / totalMs)
  for (let i = MOTION_STAGES.length - 1; i >= 0; i--) {
    if (r >= MOTION_STAGES[i].p) return { ...MOTION_STAGES[i], synthetic: r }
  }
  return { ...MOTION_STAGES[0], synthetic: r }
}

// ALD 24/05/2026 - Lazy-init SSE stream singleton (auto reconnect, no FE polling).
let motionStreamInstance = null
function getMotionStream() {
  if (!motionStreamInstance) motionStreamInstance = useMotionJobStream()
  return motionStreamInstance
}

async function pollPendingMotion(entryId, jobId, outputNodeId, opts = {}) {
  // ALD 24/05/2026 - SSE subscriber. opts.isResume=true khi gọi từ resumeLivePolls →
  // skip mọi toast (đang xử lý / hoàn tất / lỗi) vì user reload mới và job có thể đã xong
  // từ phiên trước → không cần spam toast. Toast chỉ fire cho transitions thật trong phiên.
  const HEARTBEAT_MS = 8000
  const start = Date.now()
  let lastSyntheticMsg = ''
  let lastSyntheticTs = 0
  let sawRunning = false  // chỉ toast terminal khi đã thấy job running ít nhất 1 lần trong phiên
  const expectedTotalMs = 12 * 60 * 1000
  if (!opts.isResume) {
    toast.info('Motion Transfer đang xử lý (ETA 6–22 phút)…', { duration: 4000 })
  }
  // Apply một snapshot data (từ SSE status event) lên node + entry
  function applySnapshot(data) {
    if (!data) return false
    // ALD 24/05/2026 - Chỉ patch canvas khi user đang xem ĐÚNG entry này. Background
    // SSE từ resumeLivePolls KHÔNG override canvas của user (vd reload page, entry chưa
    // được click → canvas phải clean, không hiện progress/video từ job background).
    const isViewing = selectedRunId.value === entryId
    const node = (isViewing && outputNodeId) ? nodes.value.find((n) => n.id === outputNodeId) : null
    const elapsed = Date.now() - start
    const stage = motionStageFor(elapsed, expectedTotalMs)
    const beProgress = Number(data.progress) || 0
    const effectiveProgress = Math.max(beProgress, stage.synthetic)
    const pct = Math.round(effectiveProgress * 100)
    const stepLabel = (data.current_step && data.current_step !== 'queued') ? data.current_step : stage.label
      if (node) {
        node.data = {
          ...node.data,
          _runState: data.status === 'done' ? 'success' : data.status === 'error' ? 'error' : 'running',
          _runOutput: {
            ...(node.data._runOutput || {}),
            progress: effectiveProgress,
            current_step: stepLabel,
            job_status: data.status,
          },
        }
      }
      // Update run entry + emit progress events
      const entry = testHistory.value.find((r) => r.id === entryId)
      if (entry) {
        const newMsg = `${stepLabel} (${pct}%)`
        const shouldEmit =
          newMsg !== lastSyntheticMsg ||
          Date.now() - lastSyntheticTs > HEARTBEAT_MS
        const newEvents = entry.events || []
        if (shouldEmit) {
          newEvents.push({ ts: Date.now(), level: 'info', msg: newMsg, node_id: outputNodeId })
          lastSyntheticMsg = newMsg
          lastSyntheticTs = Date.now()
        }
        patchEntry(entryId, {
          status: (data.status === 'queued' || data.status === 'running') ? 'running' : entry.status,
          events: newEvents,
          output: {
            ...(entry.output || {}),
            text: `[${data.status}] ${stepLabel} — ${pct}% · job ${jobId.slice(0, 8)}`,
            metadata: {
              ...(entry.output?.metadata || {}),
              progress: effectiveProgress,
              current_step: stepLabel,
              job_status: data.status,
            },
          },
          durationMs: elapsed,
        })
      }
    // Track running → terminal transition để chỉ toast cho job đang chạy chuyển done/error/cancel
    if (data.status === 'running' || data.status === 'queued') sawRunning = true
    const shouldToast = !opts.isResume && sawRunning
    if (data.status === 'done') {
      const url = data.output_url || ''
      const mediaKey = 'video'
      if (node) {
        node.data = {
          ...node.data,
          _runOutput: { ...(node.data._runOutput || {}), [mediaKey]: url, output_path: data.output_path, pending: false },
          _runState: 'success',
        }
      }
      // Patch luôn producer node Motion Transfer bằng URL output → user thấy video
      // chỉ ở Output. Engine emit producerJobId trong event.extra khi queue job.
      const entryRef = testHistory.value.find((r) => r.id === entryId)
      const producerEvent = (entryRef?.events || []).find((e) => e.extra?.producerJobId === jobId)
      if (producerEvent?.node_id) {
        const producerNode = nodes.value.find((n) => n.id === producerEvent.node_id)
        if (producerNode && producerNode.id !== outputNodeId) {
          producerNode.data = {
            ...producerNode.data,
            _runOutput: { ...(producerNode.data._runOutput || {}), [mediaKey]: url, output_path: data.output_path, pending: false },
            _runState: 'success',
          }
        }
      }
      const entryDone = testHistory.value.find((r) => r.id === entryId)
      if (entryDone) {
        patchEntry(entryId, {
          status: 'success',
          _live: false,
          output: {
            ...(entryDone.output || {}),
            text: url,
            metadata: { ...(entryDone.output?.metadata || {}), pending: false, [mediaKey]: url, progress: 1, job_status: 'done' },
          },
        })
      }
      if (shouldToast) toast.success('Motion Transfer hoàn tất!', { duration: 5000 })
      // ALD 25/05/2026 - Push notification cho job done. Persist qua localStorage,
      // hiện ở bell icon trên top bar layout (mọi page). Click noti → navigate workflow.
      if (sawRunning) noti.push({
        kind: 'done',
        title: 'Motion Transfer hoàn tất',
        body: `Job ${jobId.slice(0, 8)} · video sẵn sàng`,
        jobId,
        workflowId: route.params.id,
      })
      getMotionStream().unsubscribe(jobId)
      return true
    }
    if (data.status === 'error') {
      if (node) node.data = { ...node.data, _runState: 'error' }
      const entryErr = testHistory.value.find((r) => r.id === entryId)
      if (entryErr) patchEntry(entryId, {
        status: 'error',
        error: data.error || 'Motion Transfer job error',
        // ALD 24/05/2026 - Clear pending flag để drawer "Job đang chạy" ẩn đi.
        output: {
          ...(entryErr.output || {}),
          metadata: { ...(entryErr.output?.metadata || {}), pending: false, job_status: 'error' },
        },
      })
      if (shouldToast) toast.error(`Motion Transfer lỗi: ${data.error || ''}`, { duration: 6000 })
      if (sawRunning) noti.push({
        kind: 'error',
        title: 'Motion Transfer lỗi',
        body: (data.error || 'Job thất bại').split('\n')[0].slice(0, 120),
        jobId,
        workflowId: route.params.id,
      })
      getMotionStream().unsubscribe(jobId)
      return true
    }
    if (data.status === 'cancelled') {
      if (node) node.data = { ...node.data, _runState: 'warn' }
      const entryCanc = testHistory.value.find((r) => r.id === entryId)
      if (entryCanc) patchEntry(entryId, {
        status: 'error',
        error: 'Job đã huỷ',
        output: {
          ...(entryCanc.output || {}),
          metadata: { ...(entryCanc.output?.metadata || {}), pending: false, job_status: 'cancelled' },
        },
      })
      if (shouldToast) toast.info('Motion Transfer đã huỷ', { duration: 4000 })
      if (sawRunning) noti.push({
        kind: 'cancel',
        title: 'Motion Transfer đã huỷ',
        body: `Job ${jobId.slice(0, 8)} cancelled bởi user`,
        jobId,
        workflowId: route.params.id,
      })
      getMotionStream().unsubscribe(jobId)
      return true
    }
    return false
  }

  getMotionStream().subscribe(jobId, {
    onStatus: (s) => { applySnapshot({
      // BE include output_url signed khi terminal để FE bind video ngay, không cần reload.
      status: s.status, progress: s.progress, current_step: s.current_step,
      output_url: s.output_url || null, output_path: s.output_path,
      error: s.error,
    })},
    onEnd: () => { /* unsubscribe đã handle trong applySnapshot khi terminal */ },
    onWarn: (w) => console.warn('[motion-stream] warn:', w),
  })
}

// Test async — push entry với _live:true NGAY khi kick xong, poll loop update.
// Reload vẫn giữ entry trong localStorage, mount sẽ resume poll.
async function doTestRun() {
  // ALD 17/06/2026 - đọc cờ resume ("Tiếp tục") rồi RESET ngay → các run sau mặc định = chạy mới (fresh).
  const resumeFlag = pendingResume.value
  pendingResume.value = false
  // ALD 26/05/2026 - Re-check ở doTestRun vì còn 2 path khác bypass openTestRun:
  // (1) modal "Chạy với input" submit, (2) keyboard shortcut Cmd+Enter trong textarea.
  if (!validateLibraryInputs()) return
  testRunOpen.value = false
  drawerVisible.value = true
  for (const n of nodes.value) if (n.data) n.data._runState = 'idle'

  // ALD 28/05/2026 - Auto-save workflow nếu dirty trước khi run. Lý do: input nodes
  // có thể đã upload file thành công (staticUrl set trong FE memory) nhưng chưa save
  // vào DB. Run với _testDef snapshot OK nhưng next reload mất URL → click history
  // entry thấy input empty. Auto-save đảm bảo state input persist vào DB workflow
  // definition, kể cả khi user quên click "Lưu".
  // ALD 01/06/2026 - CHỈ auto-save khi là chủ workflow. Non-owner chạy workflow public:
  // bỏ qua save (PUT owner-only → 403 "Không có quyền"); definition vẫn gửi nguyên qua
  // /test bên dưới nên run vẫn đủ input. Trước đây save vô điều kiện → non-owner dính 403.
  if (dirty.value && !saving.value && isOwned.value) {
    try {
      await onSave()
    } catch (e) {
      console.warn('[doTestRun] auto-save fail (continue run anyway):', e?.message || e)
    }
  }

  const definition = currentDefinition()
  const input = { text: testInput.value }

  // ALD 26/05/2026 - Total static file ≤ 25MB OK (raw bytes). Tính raw size từ
  // base64 (length × 0.75). User upload nhiều file static cùng workflow vẫn chạy
  // nếu tổng dưới ngưỡng. Quá ngưỡng → bảo user dùng Library source (/storage).
  const sendDef = JSON.parse(JSON.stringify(definition))
  let totalBytes = 0
  for (const n of sendDef.nodes || []) {
    const c = n.data?.config
    if (c?.source === 'static' && c.staticData) totalBytes += Math.floor(c.staticData.length * 0.75)
  }
  const STATIC_TOTAL_LIMIT = 25 * 1024 * 1024
  if (totalBytes > STATIC_TOTAL_LIMIT) {
    const mb = (totalBytes / 1024 / 1024).toFixed(1)
    toast.error(
      `Tổng file static ${mb}MB > 25MB. Upload qua /storage → đổi node source = Library để chạy workflow.`,
      { duration: 8000 }
    )
    testRunning.value = false
    runningStartTs.value = 0
    return
  }
  const startTs = Date.now()
  const entryId = `${startTs}-${Math.random().toString(36).slice(2, 6)}`

  // Push entry ngay với status='running' để UI thấy job + persist localStorage
  const entry = {
    id: entryId,
    ts: startTs,
    durationMs: 0,
    input,
    triggers: getInputTriggers(definition),
    status: 'running',
    error: null,
    output: null,
    events: [],
    snapshot: { nodeCount: definition.nodes.length, edgeCount: definition.edges.length },
    _live: true,
    _runId: null
  }
  testHistory.value = [entry, ...testHistory.value].slice(0, 20)
  selectedRunId.value = entryId
  if (!openTabIds.value.includes(entryId)) openTabIds.value.push(entryId)  // pin tab multi-run
  persistTestHistory()
  testRunning.value = true
  runningStartTs.value = startTs

  // Kick async
  let runId
  try {
    const kick = await wf.test(route.params.id, sendDef, input, { resume: resumeFlag })
    runId = kick?.run_id
    if (!runId) throw new Error('Server không trả run_id')
    patchEntry(entryId, { _runId: runId })
  } catch (err) {
    const errMsg = (err?.name === 'AbortError' || err?.name === 'TimeoutError')
      ? 'Kick test timeout — kiểm tra server.'
      : (err?.message?.includes('Failed to fetch') ? `Mất kết nối tới server: ${err.message}` : (err?.data?.error || err?.message || 'Kick fail không rõ'))
    patchEntry(entryId, { status: 'error', error: errMsg, durationMs: Date.now() - startTs, _live: false })
    toast.error(errMsg, { duration: 6000 })
    testRunning.value = false
    runningStartTs.value = 0
    return
  }

  // Poll cho đến khi xong
  await pollRunUntilDone(entryId, runId, startTs)
}

// Resume polling khi user reload page mà có entry còn _live=true
// ALD 24/05/2026 - Bỏ cap 30 phút stale (quá hẹp — Wan HQ 22p + queue có thể >30p).
// reconcileStalePendingJobs sync trạng thái thật từ Motion Transfer backend nên
// FE không cần đoán "stale" bằng wall clock — BE là source of truth.
function resumeLivePolls() {
  const live = (testHistory.value || []).filter((r) => r._live && r._runId)
  for (const entry of live) {
    console.log('[resume] resume poll for', entry._runId)
    pollRunUntilDone(entry.id, entry._runId, entry.ts)   // fire-and-forget, parallel safe
  }

  // Resume SSE cho Motion Transfer async jobs (job_id trong metadata).
  const pendingMotion = (testHistory.value || []).filter((r) => {
    const m = r.output?.metadata
    const isAsync = m && m.job_id && (!m.kind || m.kind === 'motion')
    return isAsync && !m.video && effectiveRunStatus(r) === 'running'
  })
  for (const entry of pendingMotion) {
    const jobId = entry.output.metadata.job_id
    const outputNode = nodes.value.find((n) => n.data?.type === 'output')
    console.log('[resume] resume motion poll for', jobId)
    pollPendingMotion(entry.id, jobId, outputNode?.id, { isResume: true })
  }
}

// Mô tả các Input node thực tế (source + nguồn data) cho test history.
// Replace "(empty)" cho user UX rõ hơn.
function getInputTriggers(def) {
  const inputs = (def.nodes || []).filter((n) => {
    const t = n.type
    return t === 'input' || t === 'inputText' || t === 'inputImage' || t === 'inputFile' || t === 'inputHistory'
  })
  return inputs.map((n) => {
    const c = n.data?.config || {}
    const ct = c.contentType || ({ inputText: 'text', inputImage: 'image', inputFile: 'file', inputHistory: 'history' }[n.type] || 'text')
    const source = c.source || 'session'
    let detail = ''
    if (source === 'session') detail = `session.${c.field || ct}`
    else if (source === 'url') {
      try { detail = new URL(c.url || '').hostname + new URL(c.url || '').pathname.slice(0, 40) } catch { detail = c.url?.slice(0, 50) || '(no url)' }
    }
    else if (source === 'static') {
      if (ct === 'text') detail = c.staticText?.slice(0, 60) || '(empty)'
      else detail = c.staticName || '(no upload)'
    }
    return { nodeId: n.id, contentType: ct, source, detail }
  })
}

// Build definition từ in-memory state — giống logic onSave nhưng KHÔNG save BE
function currentDefinition() {
  return {
    nodes: nodes.value.map((n) => ({
      id: n.id, type: n.data.type, position: n.position,
      data: { config: normalizeMotionSegmentConfig(n.data.type, n.data.config || {}) }
    })),
    edges: edges.value.map((e) => ({
      id: e.id, source: e.source, target: e.target,
      sourceHandle: e.sourceHandle,
      // targetHandle giữ đúng slot cho các node nhiều input.
      targetHandle: e.targetHandle,
      data: e.data || (e.label ? { label: e.label } : undefined)
    }))
  }
}

// Test history persistence
// ALD 24/05/2026 - Test history giờ source of truth là BE workflow_runs.
// loadTestHistory() fetch list từ /workflows/:id/runs (đã include output + events).
// persistTestHistory() KHÔNG còn lưu localStorage — engine tự update workflow_runs khi
// emit events. Chỉ giữ localStorage làm offline fallback (legacy compat).
async function loadTestHistory() {
  try {
    const auth = useAuth()
    const res = await auth.beFetch(`/workflows/${route.params.id}/runs`)
    const items = res?.items || []
    // Convert workflow_runs row → testHistory entry shape (BE -> FE mapping)
    testHistory.value = items.map((row) => {
      // ALD 24/05/2026 - Workflow_runs.status='success' nhưng output.metadata.pending=true
      // → Motion Transfer async chưa xong. UI phải hiện 'Running' badge nhất quán
      // với drawer pending + sidebar widget, không phải 'OK' (gây hiểu lầm).
      const m = row.output?.metadata
      // ALD 24/05/2026 - Bug fix: row.status='cancelled' fallback thành 'running' badge.
      // Trước: fallback `row.status === 'success' ? 'success' : 'running'` quy mọi state
      // không-success về Running → cancelled hiển thị "15m2s elapsed". Giờ check explicit:
      //   queued/running → running (UI poll), success → success, else → error (fail badge).
      const isInflight = row.status === 'queued' || row.status === 'running'
      const isPending = isLiveJobMeta(m)
      const isErrored = !isPending && (row.status === 'error' || (m && m.job_status === 'error'))
      const isCancelled = !isPending && (row.status === 'cancelled' || (m && m.job_status === 'cancelled'))
      const derivedStatus = isInflight || isPending ? 'running'
                          : isErrored || isCancelled ? 'error'
                          : row.status === 'success' && !isPending ? 'success'
                          : 'error'
      return {
        id: row.id,
        _runId: row.id,
        _live: isPending || row.status === 'queued' || row.status === 'running',
        status: derivedStatus,
        ts: new Date(row.started_at || row.finished_at || Date.now()).getTime(),
        durationMs: row.finished_at && row.started_at && !isPending
          ? new Date(row.finished_at).getTime() - new Date(row.started_at).getTime()
          : null,
        input: row.input || {},
        output: row.output || null,
        events: row.events || [],
        error: row.error_msg || null,
      }
    })
    // Reconcile Motion Transfer pending để cập nhật video URL khi worker xong.
    reconcileStalePendingJobs()
  } catch (e) {
    console.warn('[testHistory] fetch BE fail, fallback localStorage:', e)
    try {
      const raw = localStorage.getItem(`wf:test:${route.params.id}`)
      if (raw) testHistory.value = JSON.parse(raw) || []
    } catch { testHistory.value = [] }
  }
}

async function reconcileStalePendingJobs() {
  const auth = useAuth()
  // Chỉ theo dõi kind='motion'; các job type video legacy đã được gỡ.
  const stale = (testHistory.value || []).filter((r) => {
    const m = r.output?.metadata
    // Bỏ qua job đã kết luận done-nhưng-mất-output (đã mark error) → tránh re-poll vô ích.
    if (m && m.job_status === 'done_no_output') return false
    return m && m.job_id && (!m.kind || m.kind === 'motion') && (m.pending || !m.video)
  })
  if (!stale.length) return
  // Fetch song song Motion Transfer job detail cho từng entry.
  await Promise.all(stale.map(async (entry) => {
    try {
      const job = await auth.apiFetch(`/functions/v1/motion-jobs/${entry.output.metadata.job_id}`)
      if (!job) return
      const wasPending = entry.output.metadata.pending
      if (job.status === 'done' && job.output_url) {
        // ALD 24/05/2026 - stop_after_tryon mode → output là PNG, set metadata.image.
        // Bình thường output là MP4 → metadata.video. Detect theo extension.
        const isImage = /\.(png|jpe?g|webp)(\?|$)/i.test(job.output_path || job.output_url)
        patchEntry(entry.id, {
          status: 'success',
          output: {
            ...entry.output,
            text: job.output_url,
            metadata: {
              ...entry.output.metadata,
              pending: false,
              [isImage ? 'image' : 'video']: job.output_url,
              output_path: job.output_path,
              progress: 1,
              job_status: 'done',
            },
          },
        })
      } else if (job.status === 'done' && !job.output_url) {
        // ALD 29/05/2026 - Job DONE nhưng KHÔNG có output_url (file output thiếu / đã bị xoá /
        // không ký được signed URL). TRƯỚC ĐÂY rơi qua MỌI nhánh → entry kẹt 'pending' →
        // canvas hiện "processing" vô hạn thay vì video. Mark error rõ ràng để user chạy lại.
        patchEntry(entry.id, {
          status: 'error',
          error: 'Job đã hoàn tất nhưng không tìm thấy file video output (có thể đã bị xoá hoặc lỗi upload). Vui lòng chạy lại.',
          output: {
            ...entry.output,
            metadata: { ...entry.output.metadata, pending: false, job_status: 'done_no_output' },
          },
        })
      } else if (job.status === 'error') {
        patchEntry(entry.id, {
          status: 'error',
          error: job.error || 'Motion Transfer job failed',
          output: {
            ...entry.output,
            metadata: { ...entry.output.metadata, pending: false, job_status: 'error' },
          },
        })
      } else if (job.status === 'cancelled') {
        patchEntry(entry.id, {
          status: 'error',
          error: 'Job cancelled',
          output: {
            ...entry.output,
            metadata: { ...entry.output.metadata, pending: false, job_status: 'cancelled' },
          },
        })
      }
      // ALD 27/05/2026 - Stuck detection: job ở 'queued'/'running' với progress=0 quá
      // STUCK_THRESHOLD_MS → worker không phản hồi (offline hoặc input file đã bị xoá).
      // Auto-mark error để badge "Job đang chạy" ẩn đi + user thấy lý do thay vì loading vô hạn.
      else if (job.status === 'queued' || job.status === 'running') {
        const progress = Number(job.progress) || 0
        const currentStep = job.current_step || job.step || entry.output.metadata.current_step || ''
        patchEntry(entry.id, {
          status: 'running',
          error: null,
          _live: true,
          output: {
            ...entry.output,
            text: `[${job.status}] ${currentStep || 'processing'} — ${Math.round(progress * 100)}% · job ${entry.output.metadata.job_id.slice(0, 8)}`,
            metadata: {
              ...entry.output.metadata,
              pending: true,
              progress,
              current_step: currentStep,
              job_status: job.status,
            },
          },
        })
        const outputNode = nodes.value.find((n) => n.data?.type === 'output')
        pollPendingMotion(entry.id, entry.output.metadata.job_id, outputNode?.id, { isResume: true })
        if (progress > 0) return
        const STUCK_THRESHOLD_MS = 10 * 60 * 1000  // 10 phút
        const elapsedMs = Date.now() - (entry.ts || 0)
        if (elapsedMs > STUCK_THRESHOLD_MS) {
          patchEntry(entry.id, {
            status: 'error',
            error: `Job stuck > ${Math.round(STUCK_THRESHOLD_MS / 60000)} phút ở 0% — worker không phản hồi. Có thể input/audio file đã bị xoá khỏi library. Cancel + chạy lại với asset hợp lệ.`,
            output: {
              ...entry.output,
              metadata: { ...entry.output.metadata, pending: false, job_status: 'stuck' },
            },
          })
        } else if (!wasPending) {
          // Edge case: BE entry lost pending flag — restore (chỉ khi chưa stuck)
          patchEntry(entry.id, {
            output: { ...entry.output, metadata: { ...entry.output.metadata, pending: true, job_status: job.status } },
          })
        }
      }
    } catch (e) {
      console.warn('[reconcile] motion-jobs fetch fail for entry', entry.id, e)
    }
  }))
}
// Local-only patch (đợi sync round-trip BE thì lag) — engine cũng tự lưu BE qua emit.
function persistTestHistory() {
  try {
    localStorage.setItem(`wf:test:${route.params.id}`, JSON.stringify(testHistory.value))
  } catch { /* quota exceeded — silent */ }
}
// ALD 24/05/2026 - Xoá 1 run riêng lẻ. Confirm trước khi delete BE + remove khỏi list.
// ALD 27/05/2026 - Chỉ gọi BE khi entry có _runId (UUID workflow_runs.id).
// Entries chỉ có id local (`Date.now()-rand`, kick /test fail trước khi nhận run_id)
// thì remove thẳng local — tránh DELETE 404 do BE regex /[a-f0-9-]{36}/ reject.
async function deleteSingleRun(run) {
  if (!run) return
  const ok = await confirmDialog.ask({
    title: 'Xoá run này?',
    message: `Xoá vĩnh viễn run ${fmtTime(run.ts)} (${run.status}). Không khôi phục được.`,
    confirmText: 'Xoá',
    cancelText: 'Huỷ',
    variant: 'danger',
  })
  if (!ok) return
  try {
    if (run._runId) {
      const auth = useAuth()
      await auth.beFetch(`/workflows/runs/${run._runId}`, { method: 'DELETE' })
    }
    testHistory.value = testHistory.value.filter((r) => r.id !== run.id)
    if (selectedRunId.value === run.id) selectedRunId.value = testHistory.value[0]?.id || null
    persistTestHistory()
    toast.success('Đã xoá run')
  } catch (e) {
    toast.error(`Xoá fail: ${e?.message || e}`)
  }
}

// ALD 24/05/2026 - Clear giờ delete cả workflow_runs ở BE (trước đó chỉ xoá local +
// localStorage → reload bị khôi phục từ BE). BE chỉ delete row terminal (success/error/
// cancelled) — runs còn queued/running giữ lại để FE vẫn theo dõi job đang chạy.
async function clearTestHistory() {
  const ok = await confirmDialog.ask({
    title: 'Xoá toàn bộ test history?',
    message: `Sẽ xoá vĩnh viễn ${testHistory.value.length} run đã chạy của workflow này. Job đang chạy vẫn được giữ lại.`,
    confirmText: 'Xoá',
    cancelText: 'Huỷ',
    variant: 'danger',
  })
  if (!ok) return
  try {
    const auth = useAuth()
    const res = await auth.beFetch(`/workflows/${route.params.id}/runs`, { method: 'DELETE' })
    const deleted = res?.deleted ?? 0
    // Giữ lại entries pending/running (BE chưa xoá), bỏ entries terminal
    testHistory.value = (testHistory.value || []).filter((r) => effectiveRunStatus(r) === 'running')
    if (!testHistory.value.find((r) => r.id === selectedRunId.value)) selectedRunId.value = null
    try { localStorage.removeItem(`wf:test:${route.params.id}`) } catch {}
    toast.success(`Đã xoá ${deleted} test run`)
  } catch (e) {
    toast.error(`Xoá thất bại: ${e?.message || e}`)
  }
}

const config = useRuntimeConfig()

// #region ALD 22/05/2026 - API modal: Sync + Async config + cURL previews
// Một modal duy nhất gộp cả Sync & Async API. Async toggle OFF → chỉ hiện
// Sync cURL. Toggle ON → hiện thêm callback URL + headers + Async cURL + payload.
const asyncModalOpen = ref(false)
const asyncSaving = ref(false)
const asyncConfig = reactive({
  async_enabled: false,
  callback_url: '',
  // List dạng [{ key, value }] cho UI add/remove — convert sang object khi save
  callback_headers_list: []
})

function openAsyncModal() {
  if (!workflow.value) return
  asyncConfig.async_enabled = !!workflow.value.async_enabled
  asyncConfig.callback_url = workflow.value.callback_url || ''
  const headers = workflow.value.callback_headers || {}
  asyncConfig.callback_headers_list = Object.entries(headers).map(([key, value]) => ({ key, value: String(value) }))
  if (asyncConfig.callback_headers_list.length === 0) {
    asyncConfig.callback_headers_list.push({ key: '', value: '' })  // start với 1 row trống
  }
  asyncModalOpen.value = true
}
function addHeader() {
  asyncConfig.callback_headers_list.push({ key: '', value: '' })
}
function removeHeader(idx) {
  asyncConfig.callback_headers_list.splice(idx, 1)
  if (asyncConfig.callback_headers_list.length === 0) {
    asyncConfig.callback_headers_list.push({ key: '', value: '' })
  }
}

// Headers object đã sanitize (bỏ row trống) — dùng cho cả preview lẫn save
const asyncHeadersObj = computed(() => {
  const obj = {}
  for (const { key, value } of asyncConfig.callback_headers_list) {
    const k = String(key || '').trim()
    if (k && typeof value === 'string') obj[k] = value
  }
  return obj
})

// Sample payload từ Input nodes (dùng cho cả Sync + Async cURL preview).
// Output: { payloadStr: <json-pretty>, notes: <string[]> }
//   - Session inputs → fields trong payload với example theo content type
//   - URL/static inputs → KHÔNG cần truyền payload; thêm vào notes
//   - Nếu workflow chưa có input node nào → fallback shape mẫu (text/image/file)
//     để API consumer hình dung shape upload base64 từ device
function sampleValueFor(contentType) {
  if (contentType === 'text') return 'Nội dung user message'
  if (contentType === 'history') return [{ role: 'user', content: 'Câu hỏi trước' }, { role: 'assistant', content: 'Trả lời trước' }]
  if (contentType === 'image') return { name: 'image.png', mimeType: 'image/png', data: '<base64-encoded-image>' }
  // file (default)
  return { name: 'document.pdf', mimeType: 'application/pdf', data: '<base64-encoded-file>' }
}

const samplePayload = computed(() => {
  const def = currentDefinition()
  const inputNodes = (def.nodes || []).filter((n) => /^input/.test(n.type))
  const fields = []
  const notes = []

  if (inputNodes.length === 0) {
    // Workflow chưa có input node → show generic shape mẫu để dev biết shape upload
    return {
      payloadStr: JSON.stringify({
        text: 'Nội dung user message',
        image: sampleValueFor('image'),
        file: sampleValueFor('file')
      }, null, 2),
      notes: [
        'Workflow chưa có Input node — payload trên chỉ là shape mẫu.',
        'File/image upload từ device: read → base64 → đặt vào field "data".'
      ]
    }
  }

  for (const n of inputNodes) {
    const c = n.data?.config || {}
    const ct = c.contentType || ({ inputText: 'text', inputImage: 'image', inputFile: 'file', inputHistory: 'history' }[n.type] || 'text')
    const field = c.field || ct
    const source = c.source || 'session'
    if (source === 'session') {
      fields.push([field, sampleValueFor(ct)])
    } else if (source === 'url') {
      const u = c.url ? `"${c.url.slice(0, 60)}${c.url.length > 60 ? '…' : ''}"` : '(chưa set)'
      notes.push(`Input "${field}" (${ct}) → source=url ${u} — App AI tự fetch khi chạy, KHÔNG cần đưa vào payload.`)
    } else if (source === 'static') {
      notes.push(`Input "${field}" (${ct}) → source=static (embed trong workflow) — KHÔNG cần đưa vào payload.`)
    }
  }
  return {
    payloadStr: fields.length === 0 ? '{}' : JSON.stringify(Object.fromEntries(fields), null, 2),
    notes
  }
})

function curlWithPayload(url, responseComment) {
  const { payloadStr, notes } = samplePayload.value
  const notesBlock = notes.length === 0 ? '' : '\n\n' + notes.map((n) => `# - ${n}`).join('\n')
  return `curl -X POST '${url}' \\
  -H 'x-api-key: <YOUR_API_KEY>' \\
  -H 'Content-Type: application/json' \\
  -d '${payloadStr}'

${responseComment}${notesBlock}`
}

const syncCurlPreview = computed(() => {
  if (!workflow.value) return ''
  const url = `${config.public.motionBackendUrl}/workflows/${workflow.value.slug}/invoke`
  return curlWithPayload(
    url,
    `# Response chứa output luôn (block đến khi workflow xong):
# { "run_id": "<uuid>", "status": "success", "output": { ... }, "events": [ ... ] }`
  )
})

const asyncCurlPreview = computed(() => {
  if (!workflow.value) return ''
  const url = `${config.public.motionBackendUrl}/workflows/${workflow.value.slug}/invoke-async`
  return curlWithPayload(
    url,
    `# Response 202 ngay (không chờ):
# { "job_id": "<uuid>", "status": "queued", "poll_url": "/workflows/runs/<uuid>" }`
  )
})

const asyncCallbackPreview = computed(() => {
  if (!workflow.value) return ''
  const headers = { 'Content-Type': 'application/json', 'X-Webhook-Job-Id': '<uuid>', 'X-Webhook-Attempt': '1', ...asyncHeadersObj.value }
  const headerLines = Object.entries(headers).map(([k, v]) => `${k}: ${v}`).join('\n')
  const sample = {
    job_id: '<uuid>',
    workflow_slug: workflow.value.slug,
    status: 'success',
    output: { text: 'Kết quả workflow', metadata: {} },
    error_msg: null,
    started_at: '2026-05-22T09:00:00Z',
    finished_at: '2026-05-22T09:00:42Z'
  }
  return `POST ${asyncConfig.callback_url || '<callback_url>'}
${headerLines}

${JSON.stringify(sample, null, 2)}`
})

function copyToClipboard(text, successMsg = 'Đã copy') {
  copyText(text).then(
    () => toast.success(successMsg),
    () => toast.error('Copy thất bại')
  )
}

// Download output dưới dạng .txt — tránh khi text quá lớn render UI chậm
function downloadOutput(run) {
  if (!run?.output?.text) return
  const blob = new Blob([run.output.text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `workflow-output-${run.id}.txt`
  document.body.appendChild(a)
  a.click()
  setTimeout(() => { URL.revokeObjectURL(url); a.remove() }, 100)
  toast.success(`Đã tải ${run.output.text.length.toLocaleString()} chars`)
}

async function saveAsyncConfig() {
  if (asyncSaving.value) return
  // Validate
  if (asyncConfig.async_enabled) {
    const u = String(asyncConfig.callback_url || '').trim()
    if (!u) {
      toast.error('Vui lòng nhập Callback URL')
      return
    }
    if (!/^https?:\/\//i.test(u)) {
      toast.error('Callback URL phải bắt đầu bằng http:// hoặc https://')
      return
    }
  }
  asyncSaving.value = true
  try {
    const patch = {
      async_enabled: asyncConfig.async_enabled,
      callback_url: asyncConfig.async_enabled ? asyncConfig.callback_url.trim() : null,
      callback_headers: asyncHeadersObj.value
    }
    const updated = await wf.update(route.params.id, patch)
    if (updated) workflow.value = { ...workflow.value, ...updated }
    toast.success('Đã lưu config async')
    asyncModalOpen.value = false
  } catch (err) {
    toast.error(err.data?.error || err.message || 'Lưu thất bại')
  } finally {
    asyncSaving.value = false
  }
}
// #endregion

function fmtTime(ts) {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}
// Tiêu đề row history — ưu tiên session text user gõ, fallback trigger detail đầu tiên
function historyItemTitle(r) {
  if (r.input?.text) return r.input.text.slice(0, 42)
  const firstTrigger = r.triggers?.[0]
  if (firstTrigger) return `${firstTrigger.source}: ${firstTrigger.detail.slice(0, 32)}`
  return '(empty)'
}

// Time relative to run start — vd "+0.3s" giúp đọc timeline dễ
function fmtRelTime(ts, runStart) {
  const ms = ts - runStart
  if (ms < 1000) return `+${ms}ms`
  return `+${(ms / 1000).toFixed(1)}s`
}

function fmtMs(ms) {
  if (!ms || ms < 1000) return `${ms || 0}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m${Math.round((ms % 60000) / 1000)}s`
}

const InspectorGpuWarmup = defineAsyncComponent(() => import('~/components/workflow/InspectorGpuWarmup.vue'))
const InspectorGpuFree = defineAsyncComponent(() => import('~/components/workflow/InspectorGpuFree.vue'))
const InspectorValidate = defineAsyncComponent(() => import('~/components/workflow/InspectorValidate.vue'))
const InspectorInput = defineAsyncComponent(() => import('~/components/workflow/InspectorInput.vue'))
const InspectorOutput = defineAsyncComponent(() => import('~/components/workflow/InspectorOutput.vue'))
const InspectorWorkflow = defineAsyncComponent(() => import('~/components/workflow/InspectorWorkflow.vue'))
const InspectorCondition = defineAsyncComponent(() => import('~/components/workflow/InspectorCondition.vue'))
const InspectorHttp = defineAsyncComponent(() => import('~/components/workflow/InspectorHttp.vue'))
const InspectorMotionTransfer = defineAsyncComponent(() => import('~/components/workflow/InspectorMotionTransfer.vue'))
const InspectorTryon = defineAsyncComponent(() => import('~/components/workflow/InspectorTryon.vue'))
const InspectorCreateImage = defineAsyncComponent(() => import('~/components/workflow/InspectorCreateImage.vue'))
const InspectorEditImage = defineAsyncComponent(() => import('~/components/workflow/InspectorEditImage.vue'))  // ALD 01/07/2026
const InspectorDebug = defineAsyncComponent(() => import('~/components/workflow/InspectorDebug.vue'))
const InspectorCompose = defineAsyncComponent(() => import('~/components/workflow/InspectorCompose.vue'))
const InspectorProductOverlay = defineAsyncComponent(() => import('~/components/workflow/InspectorProductOverlay.vue'))
const InspectorTextToVideo = defineAsyncComponent(() => import('~/components/workflow/InspectorTextToVideo.vue')) // ALD 14/06/2026
const InspectorTeenFlycam = defineAsyncComponent(() => import('~/components/workflow/InspectorTeenFlycam.vue'))
const InspectorTrendTiktok = defineAsyncComponent(() => import('~/components/workflow/InspectorTrendTiktok.vue'))
const InspectorSs = defineAsyncComponent(() => import('~/components/workflow/InspectorSs.vue')) // ALD 14/06/2026 - SS: Ảnh→Video LTX-2.3 + LoRA
const InspectorTalk = defineAsyncComponent(() => import('~/components/workflow/InspectorTalk.vue'))
const InspectorVoiceover = defineAsyncComponent(() => import('~/components/workflow/InspectorVoiceover.vue')) // ALD 17/06/2026 - Lồng tiếng đọc mô tả lên clip
const InspectorWanI2v = defineAsyncComponent(() => import('~/components/workflow/InspectorWanI2v.vue')) // ALD 17/06/2026 - Ảnh → Video (Wan I2V)
const InspectorCastModel = defineAsyncComponent(() => import('~/components/workflow/InspectorCastModel.vue')) // ALD 30/06/2026 - Tuyển người mẫu từ kho
const InspectorConcat = defineAsyncComponent(() => import('~/components/workflow/InspectorConcat.vue'))
const InspectorApiKey = defineAsyncComponent(() => import('~/components/workflow/InspectorApiKey.vue')) // ALD 11/06/2026
const InspectorSubtitle = defineAsyncComponent(() => import('~/components/workflow/InspectorSubtitle.vue')) // ALD 15/06/2026 - Phụ đề + Dịch
const InspectorEnhance      = defineAsyncComponent(() => import('~/components/workflow/InspectorEnhance.vue'))      // ALD 22/06/2026 - Nâng chất lượng (upscale hậu Wan)
const InspectorReveal       = defineAsyncComponent(() => import('~/components/workflow/InspectorReveal.vue'))       // ALD 08/07/2026 - Đè lộ (reveal-overlay 2 video)

function inspectorComponent(type) {
  return {
    'gpu-warmup': InspectorGpuWarmup,
    'gpu-free': InspectorGpuFree,
    validate: InspectorValidate,
    // Legacy input* alias → InspectorInput (tự infer contentType từ nodeType prop)
    input: InspectorInput, inputText: InspectorInput, inputImage: InspectorInput,
    inputFile: InspectorInput, inputHistory: InspectorInput,
    output: InspectorOutput,
    workflow: InspectorWorkflow, condition: InspectorCondition,
    http: InspectorHttp,
    motion: InspectorMotionTransfer,
    tryon: InspectorTryon,
    'create-image': InspectorCreateImage,
    'edit-image': InspectorEditImage,
    compose: InspectorCompose,
    'product-overlay': InspectorProductOverlay,
    'text-to-video': InspectorTextToVideo, // ALD 14/06/2026
    'teen-flycam': InspectorTeenFlycam,
    'trend-tiktok': InspectorTrendTiktok,
    ss: InspectorSs, // ALD 14/06/2026 - SS: Ảnh→Video LTX-2.3 + LoRA custom
    subtitle: InspectorSubtitle, // ALD 15/06/2026 - Phụ đề + Dịch
    enhance: InspectorEnhance,           // ALD 22/06/2026 - Nâng chất lượng (upscale hậu Wan)

    talk: InspectorTalk,
    voiceover: InspectorVoiceover, // ALD 17/06/2026 - Lồng tiếng đọc mô tả lên clip
    'wan-i2v': InspectorWanI2v, // ALD 17/06/2026 - Ảnh → Video (Wan I2V)
    'cast-model': InspectorCastModel, // ALD 30/06/2026 - Tuyển người mẫu từ kho theo giới tính/tuổi
    concat: InspectorConcat,
    reveal: InspectorReveal,
    'api-key': InspectorApiKey, // ALD 11/06/2026
    debug: InspectorDebug
  }[type] || null
}
</script>

<style>
/* #region ALD 22/05/2026 - Apple HIG editor */
:root {
  --apl-bg:           #0A0A0C;   /* iOS systemGroupedBackground */
  --apl-bg-secondary: #141416;
  --apl-separator:    rgba(235, 236, 240, 0.12);
  --apl-label:        #F7F8F8;
  --apl-label-2:      rgba(235, 236, 240, 0.6);
  --apl-label-3:      rgba(235, 236, 240, 0.3);
  --apl-blue:         #5E6AD2;
  --apl-blue-dark:    #6B76E5;
  /* ALD 06/07/2026 - fill control dùng chung inspector/node (đổi theo theme) */
  --apl-fill:         rgba(255, 255, 255, 0.05);
  --apl-fill-2:       rgba(255, 255, 255, 0.09);
  /* Node card surface (FlowNode dùng — đổi theo theme) */
  --apl-node-bg:            rgba(24, 24, 28, 0.94);
  --apl-node-border:        rgba(235, 236, 240, 0.10);
  --apl-node-border-hover:  rgba(235, 236, 240, 0.28);
  --apl-node-shadow:        0 1px 2px rgba(0, 0, 0, 0.4), 0 16px 32px -8px rgba(0, 0, 0, 0.55), inset 0 0.5px 0 rgba(255, 255, 255, 0.06);
  --apl-node-shadow-hover:  0 2px 4px rgba(0, 0, 0, 0.45), 0 20px 44px -8px rgba(0, 0, 0, 0.65), inset 0 0.5px 0 rgba(255, 255, 255, 0.08);
}

.apl-editor {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: var(--apl-bg);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  letter-spacing: -0.01em;
}

/* Sidebars — frosted glass */
.apl-sidebar {
  flex-shrink: 0;
  background: rgba(15, 15, 18, 0.78);
  backdrop-filter: blur(30px) saturate(180%);
  -webkit-backdrop-filter: blur(30px) saturate(180%);
  display: flex;
  flex-direction: column;
}
.apl-sidebar-left  { width: 220px; border-right: 0.5px solid var(--apl-separator); }

/* #region ALD 07/07/2026 - Mobile drawer cho node palette. Desktop: nút toggle/close ẩn,
   sidebar là cột tĩnh 220px. Mobile (<820px): sidebar thành off-canvas overlay + nền mờ. */
.apl-palette-toggle,
.apl-palette-close { display: none; }
.apl-palette-toggle {
  width: 34px; height: 34px; flex-shrink: 0;
  align-items: center; justify-content: center;
  background: var(--apl-fill); color: var(--apl-label);
  border: 0.5px solid var(--apl-separator); border-radius: 9px;
  font-size: 17px; cursor: pointer;
}
.apl-palette-toggle:active { background: var(--apl-fill-2); }
.apl-palette-close {
  margin-left: auto; width: 30px; height: 30px;
  align-items: center; justify-content: center;
  background: transparent; color: var(--apl-label); opacity: 0.7;
  border: none; border-radius: 8px; font-size: 15px; cursor: pointer;
}
.apl-palette-close:active { background: var(--apl-fill-2); }
.apl-inspector-close {
  display: none; width: 28px; height: 28px;
  align-items: center; justify-content: center;
  background: transparent; color: var(--apl-label); opacity: 0.7;
  border: none; border-radius: 7px; font-size: 14px; cursor: pointer;
}
.apl-inspector-close:active { background: var(--apl-fill-2); }

@media (max-width: 819px) {
  .apl-sidebar-left {
    position: fixed; top: 0; left: 0; bottom: 0; z-index: 60;
    width: min(82vw, 300px);
    transform: translateX(-100%);
    transition: transform 0.32s cubic-bezier(0.32, 0.72, 0, 1);
    box-shadow: 0 0 48px rgba(0, 0, 0, 0.55);
  }
  .apl-sidebar-left.is-open { transform: translateX(0); }
  .apl-sidebar-header { display: flex; align-items: center; }
  .apl-palette-toggle,
  .apl-palette-close { display: inline-flex; }
  .apl-palette-backdrop {
    position: fixed; inset: 0; z-index: 55;
    background: rgba(0, 0, 0, 0.45);
    -webkit-backdrop-filter: blur(2px); backdrop-filter: blur(2px);
  }
  /* Right inspector cũng thành overlay (nếu để 360px trong flow sẽ bóp nát canvas trên phone) */
  .apl-sidebar-right { position: fixed; top: 0; right: 0; bottom: 0; z-index: 60; }
  .apl-sidebar-right.is-open { width: min(92vw, 380px); box-shadow: 0 0 48px rgba(0, 0, 0, 0.55); }
  .apl-inspector-content { width: 100%; }
  .apl-inspector-close { display: inline-flex; }
}
/* #endregion */

/* Right inspector — animate width khi node click/unselect.
   Spring easing Apple HIG (matches sidebar animations trong macOS). */
.apl-sidebar-right {
  width: 0;
  border-left: 0 solid transparent;
  overflow: hidden;
  transition: width 0.34s cubic-bezier(0.32, 0.72, 0, 1),
              border-left-width 0.34s cubic-bezier(0.32, 0.72, 0, 1);
}
.apl-sidebar-right.is-open {
  width: 360px;
  border-left: 0.5px solid var(--apl-separator);
}
.apl-inspector-content {
  width: 360px;
  height: 100%;
  overflow-y: auto;
}

/* ALD 24/05/2026 - Drawer slide transition: enter from RIGHT (slide left), leave to RIGHT.
   Switching nodes triggers leave-then-enter cycle (mode="out-in") nên drawer "đóng" rồi
   "mở lại" rõ ràng. */
.apl-inspector-enter-active {
  transition: opacity 0.26s cubic-bezier(0.32, 0.72, 0, 1),
              transform 0.34s cubic-bezier(0.32, 0.72, 0, 1);
}
.apl-inspector-leave-active {
  transition: opacity 0.18s cubic-bezier(0.32, 0.72, 0, 1),
              transform 0.22s cubic-bezier(0.32, 0.72, 0, 1);
}
.apl-inspector-enter-from {
  opacity: 0;
  transform: translateX(80px);
}
.apl-inspector-leave-to {
  opacity: 0;
  transform: translateX(80px);
}

.apl-sidebar-header {
  padding: 14px 16px 6px;
  flex-shrink: 0;
}
.apl-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--apl-blue);
  font-size: 15px;
  font-weight: 400;
  text-decoration: none;
  transition: opacity 0.15s;
}
.apl-back:hover { opacity: 0.7; }
.apl-back .bi { font-size: 17px; margin-top: 1px; }

.apl-search-wrap {
  position: relative;
  padding: 7px 12px;
  flex-shrink: 0;
}
/* ALD 06/07/2026 - Icon căn giữa CHUẨN theo input (padding wrap đối xứng + translateY -50%);
   trước đây padding lệch (4/10) + translateY(-25%) → icon lệch so với text. */
.apl-search-icon {
  position: absolute;
  left: 22px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--apl-label-3);
  font-size: 13px;
  line-height: 1;
  pointer-events: none;
}
.apl-search-input {
  width: 100%;
  height: 32px;
  padding: 0 10px 0 30px;
  background: rgba(118, 118, 128, 0.12);
  border: none;
  border-radius: 10px;
  font-size: 13px;
  color: var(--apl-label);
  outline: none;
  font-family: inherit;
}
.apl-search-input::placeholder { color: var(--apl-label-3); }
.apl-search-input:focus { background: rgba(35, 35, 41, 0.95); box-shadow: 0 0 0 3px rgba(94, 106, 210, 0.2); }

.apl-palette {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 12px 12px;
}
.apl-cat { margin-bottom: 14px; }
.apl-cat-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--apl-label-2);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0 4px 4px;
}
.apl-cat-list { display: flex; flex-direction: column; gap: 4px; }

.apl-palette-item {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px;
  background: var(--apl-bg-secondary); border-radius: 8px;
  border: 0.5px solid var(--apl-separator);
  cursor: grab; transition: background 0.15s ease, border-color 0.15s ease;
}
.apl-palette-item:hover {
  background: rgba(35, 35, 41, 0.95);
  border-color: var(--apl-label);
}
.apl-palette-item:active { cursor: grabbing; }
/* ALD 15/06/2026 - Xem lại history = read-only: palette không kéo được */
.apl-palette-item.is-disabled { opacity: 0.4; cursor: not-allowed; pointer-events: none; }
.apl-palette-icon {
  flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  width: 24px; height: 24px;
  border-radius: 6px;
  font-size: 12px;
}
.apl-palette-text { min-width: 0; flex: 1; line-height: 1.1; }
.apl-palette-label { display: block; font-size: 12px; font-weight: 600; color: var(--apl-label); letter-spacing: -0.01em; }
.apl-palette-hint  { display: block; font-size: 10.5px; color: var(--apl-label-2); margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.apl-empty { font-size: 12px; color: var(--apl-label-3); text-align: center; padding: 12px; font-style: italic; }

.apl-sidebar-footer {
  padding: 8px 16px 12px;
  font-size: 11px;
  color: var(--apl-label-2);
  border-top: 0.5px solid var(--apl-separator);
  flex-shrink: 0;
}
.apl-kbd {
  display: inline-block;
  padding: 1px 5px;
  background: var(--apl-bg-secondary);
  border: 0.5px solid var(--apl-separator);
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 10px;
  font-weight: 600;
  color: var(--apl-label);
}

/* Canvas area */
.apl-canvas-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.apl-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: rgba(15, 15, 18, 0.78);
  backdrop-filter: blur(30px) saturate(180%);
  -webkit-backdrop-filter: blur(30px) saturate(180%);
  border-bottom: 0.5px solid var(--apl-separator);
  flex-shrink: 0;
}
.apl-title    { font-size: 13px; font-weight: 600; color: var(--apl-label); letter-spacing: -0.02em; }
.apl-subtitle { font-size: 11px; color: var(--apl-label-2); font-family: ui-monospace, SFMono-Regular, monospace; margin-top: 0; }

.apl-actions { display: flex; align-items: center; gap: 10px; }

/* ALD 24/05/2026 - Redesigned topbar actions */
.apl-action-group {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  background: rgba(118, 118, 128, 0.08);
  border-radius: 10px;
  padding: 2px;
}
.apl-icon-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  border: none;
  background: transparent;
  color: var(--apl-label);
  cursor: pointer;
  font-size: 13px;
  transition: all 0.15s cubic-bezier(0.32, 0.72, 0, 1);
  text-decoration: none;
}
.apl-icon-btn:hover  { background: rgba(24, 24, 28, 0.9); color: var(--apl-label); }
.apl-icon-btn.is-active {
  background: var(--apl-bg-secondary);
  color: var(--apl-blue);
  box-shadow: 0 0.5px 1px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.08);
}
.apl-icon-btn-badge {
  position: absolute;
  top: -2px; right: -2px;
  min-width: 14px; height: 14px;
  padding: 0 3px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 999px;
  background: var(--apl-blue);
  color: white;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0;
}
.apl-icon-btn-badge.apl-badge-running { background: #FF9500; }

.apl-cta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 14px;
  border-radius: 999px;
  border: none;
  cursor: pointer;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: all 0.18s cubic-bezier(0.32, 0.72, 0, 1);
}
.apl-cta:active:not(.is-disabled):not(.is-running) { transform: scale(0.96); }
.apl-cta-secondary {
  background: rgba(118, 118, 128, 0.12);
  color: var(--apl-label);
}
.apl-cta-secondary:hover:not(.is-disabled) {
  background: rgba(118, 118, 128, 0.18);
}
.apl-cta-primary {
  background: var(--apl-blue);
  color: white;
  box-shadow: 0 1px 2px rgba(0,49,167,0.18), 0 4px 14px rgba(0,49,167,0.22);
}
.apl-cta-primary:hover:not(.is-running) {
  background: var(--apl-blue-dark);
  box-shadow: 0 1px 2px rgba(0,49,167,0.22), 0 6px 18px rgba(0,49,167,0.28);
}
.apl-cta.is-disabled {
  background: rgba(118, 118, 128, 0.08);
  color: var(--apl-label);
  cursor: not-allowed;
  box-shadow: none;
}
.apl-cta.is-running {
  background: linear-gradient(135deg, #FF9500, #FF3B30);
  color: white;
  cursor: progress;
}

/* ALD 24/05/2026 - Pending Job tracker compact */
.apl-section-pending {
  background: linear-gradient(180deg, rgba(255,149,0,0.08), rgba(255,149,0,0.02));
  border: 0.5px solid rgba(255,149,0,0.20);
}
/* ALD 24/05/2026 - 3 trạng thái cuối: cancelled (gray), error (rose), done (emerald) */
.apl-section-cancelled {
  background: linear-gradient(180deg, rgba(120,120,128,0.08), rgba(120,120,128,0.02));
  border: 0.5px solid rgba(120,120,128,0.20);
}
.apl-section-job-error {
  background: linear-gradient(180deg, rgba(255,59,48,0.08), rgba(255,59,48,0.02));
  border: 0.5px solid rgba(255,59,48,0.22);
}
.apl-section-done {
  background: linear-gradient(180deg, rgba(52,199,89,0.08), rgba(52,199,89,0.02));
  border: 0.5px solid rgba(52,199,89,0.22);
}
.apl-pending-head { gap: 6px; }
.apl-pending-kind {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 9.5px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(118,118,128,0.14);
  color: var(--apl-label);
  text-transform: lowercase;
}
.apl-pending-cancel {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 9px;
  background: rgba(255,59,48,0.10);
  color: #B91C1C;
  border: 0.5px solid rgba(255,59,48,0.22);
  border-radius: 8px;
  font-size: 10.5px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s;
}
.apl-pending-cancel:hover { background: rgba(255,59,48,0.18); color: #991B1B; }
.apl-pending-cancel i { font-size: 11px; }
.apl-pending-body { padding: 8px 2px 2px; }
.apl-pending-step {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--apl-label);
  letter-spacing: -0.005em;
  margin-bottom: 6px;
}
.apl-pending-bar {
  height: 4px;
  border-radius: 999px;
  background: rgba(255,149,0,0.14);
  overflow: hidden;
}
.apl-pending-fill {
  height: 100%;
  background: linear-gradient(90deg, #FF9500, #FFB340);
  border-radius: 999px;
  transition: width 0.5s cubic-bezier(0.32, 0.72, 0, 1);
}
.apl-pending-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  font-size: 10.5px;
  color: var(--apl-label);
}
.apl-pending-pct {
  font-weight: 700;
  color: #8A4B00;
  font-variant-numeric: tabular-nums;
}
.apl-dirty {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #F5D77A;
  font-weight: 500;
  margin-right: 4px;
}
.apl-dirty-dot { width: 5px; height: 5px; border-radius: 50%; background: #F59E0B; animation: apl-pulse-dot 1.4s infinite; }
@keyframes apl-pulse-dot { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

.apl-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 10px;
  border-radius: 7px;
  font-size: 12px;
  font-weight: 500;
  border: none;
  cursor: pointer;
  transition: all 0.15s ease;
  letter-spacing: -0.01em;
  font-family: inherit;
}
.apl-btn-ghost {
  color: var(--apl-blue);
  background: transparent;
  text-decoration: none;
}
.apl-btn-ghost:hover { background: rgba(94, 106, 210, 0.08); }
.apl-btn-primary {
  color: white;
  background: var(--apl-blue);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(94, 106, 210, 0.3);
}
.apl-btn-primary:hover { background: var(--apl-blue-dark); }
.apl-btn-primary:active { transform: scale(0.97); }
.apl-btn-disabled { color: var(--apl-label-3); background: rgba(118, 118, 128, 0.12); cursor: not-allowed; }

/* Canvas */
.apl-canvas { flex: 1; min-height: 0; position: relative; background: var(--apl-bg); overflow: hidden; }

/* ALD 06/07/2026 - Nền canvas kiểu Vercel: lưới kẻ ô hairline fade dần (::before)
   + sàn lưới 3D perspective ở đáy (::after). Chỉ trang trí — pointer-events none, nằm dưới VueFlow. */
.apl-canvas::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(255,255,255,0.07) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.07) 1px, transparent 1px);
  background-size: 96px 96px;
  -webkit-mask-image: radial-gradient(ellipse 85% 75% at 50% 38%, black 25%, transparent 78%);
  mask-image: radial-gradient(ellipse 85% 75% at 50% 38%, black 25%, transparent 78%);
}
.apl-canvas::after {
  content: "";
  position: absolute;
  left: -25%;
  right: -25%;
  bottom: -14%;
  height: 58%;
  z-index: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(94, 106, 210, 0.10) 1px, transparent 1px),
    linear-gradient(90deg, rgba(94, 106, 210, 0.10) 1px, transparent 1px);
  background-size: 56px 56px;
  transform: perspective(620px) rotateX(63deg);
  transform-origin: center bottom;
  -webkit-mask-image: linear-gradient(to top, rgba(0, 0, 0, 0.75), transparent 82%);
  mask-image: linear-gradient(to top, rgba(0, 0, 0, 0.75), transparent 82%);
}
/* VueFlow + empty-state nổi trên 2 layer trang trí */
.apl-canvas .vue-flow { position: relative; z-index: 1; background: transparent; }
.apl-canvas .apl-empty-state { z-index: 2; }

/* ALD 12/06/2026 - multi-run tab strip (kiểu VS Code) phía trên canvas */
.apl-runtabs { display: flex; align-items: flex-end; gap: 4px; padding: 8px 12px 0; background: var(--apl-bg); overflow-x: auto; flex: none; }
.apl-runtab { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; font-size: 12px; font-weight: 500; border: 1px solid rgba(255,255,255,0.09); border-bottom: none; border-radius: 10px 10px 0 0; background: #141418; color: #8A8F98; cursor: pointer; white-space: nowrap; }
.apl-runtab:hover { background: #1C1C22; }
.apl-runtab.is-active { background: var(--apl-bg-secondary); color: #F7F8F8; box-shadow: 0 -2px 6px rgba(0, 0, 0, 0.04); }
.apl-runtab.is-success > i:first-child { color: #22c55e; }
.apl-runtab.is-error > i:first-child { color: #ef4444; }
.apl-runtab.is-running > i:first-child { color: #6366f1; }
.apl-runtab-spin { animation: apl-runtab-spin 1s linear infinite; }
@keyframes apl-runtab-spin { to { transform: rotate(360deg); } }
.apl-runtab-x { opacity: 0.45; border-radius: 4px; padding: 1px; }
.apl-runtab-x:hover { opacity: 1; background: var(--apl-fill-2); }

.apl-empty-state {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  text-align: center;
  padding: 24px;
}
.apl-empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 18px;
  background: var(--apl-bg-secondary);
  border: 0.5px solid var(--apl-separator);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
  font-size: 22px;
  color: var(--apl-blue);
  margin-bottom: 12px;
}
.apl-empty-title { font-size: 15px; font-weight: 600; color: var(--apl-label); }
.apl-empty-hint  { font-size: 12px; color: var(--apl-label-2); margin-top: 2px; }

/* #region ALD 22/05/2026 - Run drawer redesigned */
.apl-run-drawer {
  flex-shrink: 0;
  /* ALD 27/05/2026 - height giờ controlled qua inline style từ drawerHeight ref.
     Bỏ max-height cứng để drag resize work cả 2 chiều. */
  background: rgba(20, 20, 24, 0.85);
  backdrop-filter: blur(30px) saturate(180%);
  -webkit-backdrop-filter: blur(30px) saturate(180%);
  border-top: 0.5px solid var(--apl-separator);
  display: flex; flex-direction: column;
  position: relative;
}

/* ALD 27/05/2026 - Drag handle trên top edge (ns-resize cursor). 8px hit area,
   3px visual line center. Hover/active → highlight blue. */
.apl-drawer-resize {
  position: absolute;
  top: -4px; left: 0; right: 0; height: 8px;
  cursor: ns-resize;
  z-index: 10;
  display: flex; align-items: center; justify-content: center;
}
.apl-drawer-resize:hover .apl-drawer-resize-line,
.apl-drawer-resize:active .apl-drawer-resize-line {
  background: var(--color-primary, #9AA2F2); height: 3px;
}
.apl-drawer-resize-line {
  width: 48px; height: 3px; border-radius: 2px;
  background: rgba(235,236,240,0.18);
  transition: background 0.12s ease, height 0.12s ease;
  pointer-events: none;
}

/* ── Drawer header (sticky) ────────────────────────────────────────── */
.apl-drawer-header {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 14px;
  border-bottom: 0.5px solid var(--apl-separator);
  flex-shrink: 0;
  background: var(--apl-fill);
}
.apl-drawer-title { font-size: 11px; font-weight: 700; color: var(--apl-label-2); text-transform: uppercase; letter-spacing: 0.05em; }
.apl-text-btn {
  background: transparent; border: none; padding: 4px 8px;
  color: var(--apl-label-2); font-size: 11px; font-weight: 600;
  cursor: pointer; border-radius: 6px; font-family: inherit;
  display: inline-flex; align-items: center;
}
.apl-text-btn:hover { background: rgba(255,59,48,0.08); color: #FF3B30; }
.apl-close { color: var(--apl-label-2); background: none; border: none; cursor: pointer; padding: 4px 6px; border-radius: 6px; }
.apl-close:hover { background: rgba(118, 118, 128, 0.12); color: var(--apl-label); }

/* ── Filter chips ──────────────────────────────────────────────────── */
.apl-filter-chips { display: inline-flex; gap: 4px; }
.apl-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px; border-radius: 999px;
  background: rgba(118,118,128,0.08);
  border: 0.5px solid transparent;
  font-size: 11px; font-weight: 600; color: var(--apl-label-2);
  cursor: pointer; font-family: inherit;
  transition: all 0.12s;
  white-space: nowrap;
}
.apl-chip:hover { background: rgba(118,118,128,0.14); color: var(--apl-label); }
.apl-chip.is-active { background: var(--apl-blue); color: white; border-color: var(--apl-blue); }
.apl-chip-success.is-active { background: #34C759; border-color: #34C759; }
.apl-chip-error.is-active { background: #FF3B30; border-color: #FF3B30; }
.apl-chip-count {
  font-size: 10px; font-weight: 700;
  padding: 0 5px; border-radius: 999px;
  background: rgba(255,255,255,0.12);
  font-variant-numeric: tabular-nums;
}
.apl-chip.is-active .apl-chip-count { background: rgba(255,255,255,0.25); }

/* ── Drawer body (2-col) ───────────────────────────────────────────── */
.apl-drawer-body { flex: 1; min-height: 0; display: flex; }

/* ── Left: run list ────────────────────────────────────────────────── */
.apl-history-list {
  width: 280px; flex-shrink: 0;
  border-right: 0.5px solid var(--apl-separator);
  overflow-y: auto;
  padding: 6px;
  display: flex; flex-direction: column; gap: 2px;
}
/* ALD 24/05/2026 - History item = main button (chiếm full row) + delete button (hover-reveal). */
.apl-history-item {
  position: relative;
  display: flex; align-items: stretch; gap: 0;
  width: 100%; border-radius: 9px;
  transition: background 0.1s;
}
.apl-history-item:hover { background: rgba(118,118,128,0.08); }
.apl-history-item.is-selected { background: rgba(94,106,210,0.1); box-shadow: 0 0 0 0.5px rgba(94,106,210,0.2); }
.apl-history-item.is-running { background: rgba(255,149,0,0.08); }
.apl-history-main {
  flex: 1; min-width: 0;
  display: flex; align-items: flex-start; gap: 8px;
  padding: 7px 9px;
  background: transparent; border: none; border-radius: 9px 0 0 9px;
  cursor: pointer; text-align: left;
  font-family: inherit;
}
.apl-history-delete {
  width: 28px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  background: transparent; border: none; border-radius: 0 9px 9px 0;
  color: var(--apl-label); cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.1s, color 0.1s;
}
.apl-history-item:hover .apl-history-delete { opacity: 1; }
.apl-history-delete:hover {
  background: rgba(255,59,48,0.12);
  color: rgb(255,59,48);
}
.apl-history-title {
  font-size: 12px; font-weight: 600; color: var(--apl-label);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-variant-numeric: tabular-nums;
}
.apl-history-meta {
  font-size: 10.5px; color: var(--apl-label-2);
  margin-top: 2px; line-height: 1.4;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.apl-history-err { color: #F29B9B; font-weight: 500; }
.apl-history-out { color: var(--apl-label-2); font-style: italic; }
.apl-history-running-text { color: #A86200; font-weight: 500; }
.apl-history-empty-list {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 20px 10px; color: var(--apl-label-3); text-align: center;
}

/* ── Status pills (compact) ────────────────────────────────────────── */
.apl-status-pill {
  display: inline-flex; align-items: center; gap: 3px;
  padding: 2px 8px; border-radius: 999px;
  font-size: 9.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.04em;
  flex-shrink: 0;
  white-space: nowrap;
}
.apl-status-pill .bi { font-size: 9px; }
.apl-pill-success { background: rgba(52,199,89,0.15); color: #7CDDAA; }
.apl-pill-error   { background: rgba(255,59,48,0.15); color: #F29B9B; }
.apl-pill-running { background: rgba(255,149,0,0.15); color: #A86200; }

/* ── Right: detail panel ───────────────────────────────────────────── */
.apl-history-detail { flex: 1; min-width: 0; display: flex; flex-direction: column; }

.apl-detail-header {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 16px;
  border-bottom: 0.5px solid var(--apl-separator);
  background: var(--apl-fill);
  flex-shrink: 0;
  flex-wrap: wrap;
}
.apl-detail-meta {
  display: inline-flex; align-items: center;
  font-size: 11px; color: var(--apl-label-2);
  font-variant-numeric: tabular-nums;
}
.apl-detail-meta .bi { font-size: 10px; }
.apl-detail-action {
  display: inline-flex; align-items: center;
  padding: 4px 10px; border-radius: 7px;
  background: var(--apl-blue); color: white;
  border: none; font-size: 11px; font-weight: 600;
  cursor: pointer; font-family: inherit;
  transition: all 0.12s;
}
.apl-detail-action:hover:not(:disabled) { background: #8FBAF0; }
.apl-detail-action:disabled { background: rgba(118,118,128,0.3); cursor: not-allowed; }
.apl-detail-cancel { background: #FF3B30; }
/* ALD 17/06/2026 - "Tiếp tục từ chỗ lỗi": xanh lá để nổi bật là hành động nên dùng khi run lỗi */
.apl-detail-resume { background: #34C759; margin-right: 6px; }
.apl-detail-resume:hover:not(:disabled) { background: #28A745; }
.apl-detail-cancel:hover:not(:disabled) { background: #C92F26; }

.apl-detail-body { flex: 1; min-height: 0; overflow-y: auto; padding: 12px 16px; }
.apl-detail-section { margin-bottom: 14px; }
.apl-detail-section:last-child { margin-bottom: 0; }
.apl-section-error {
  margin: -4px -4px 14px -4px;
  padding: 10px 12px;
  background: rgba(255,59,48,0.05);
  border: 0.5px solid rgba(255,59,48,0.18);
  border-radius: 10px;
}
.apl-section-head {
  display: flex; align-items: center; gap: 6px;
  margin-bottom: 6px;
}
.apl-section-title {
  font-size: 10.5px; font-weight: 700;
  color: var(--apl-label-2);
  text-transform: uppercase; letter-spacing: 0.06em;
}
.apl-section-badge {
  font-size: 10px; font-weight: 600;
  padding: 1px 6px; border-radius: 999px;
  background: rgba(118,118,128,0.12);
  color: var(--apl-label-2);
  font-variant-numeric: tabular-nums;
}

.apl-detail-pre {
  white-space: pre-wrap; word-break: break-word;
  background: var(--apl-bg-secondary); padding: 9px 11px; border-radius: 8px;
  font-size: 11.5px; border: 0.5px solid var(--apl-separator);
  font-family: ui-monospace, SFMono-Regular, monospace; color: var(--apl-label);
  margin: 0; line-height: 1.5;
  /* Không set max-height/overflow → để .apl-detail-body lo scroll (1 scroll only) */
}
.apl-pre-error { color: #F29B9B; background: rgba(255,59,48,0.15); border-color: rgba(255,59,48,0.2); }

/* ── Empty states ──────────────────────────────────────────────────── */
.apl-history-empty {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  color: var(--apl-label-3); padding: 20px;
  text-align: center;
}
.apl-empty-title { font-size: 13px; font-weight: 600; color: var(--apl-label-2); }
.apl-empty-hint { font-size: 11px; color: var(--apl-label-3); margin-top: 4px; line-height: 1.5; }
.apl-empty-text { font-size: 11.5px; color: var(--apl-label-3); font-style: italic; }

/* ── Loader ring (for running state right panel) ──────────────────── */
.apl-loader-ring {
  width: 36px; height: 36px;
  border: 3px solid rgba(94,106,210,0.15);
  border-top-color: var(--apl-blue);
  border-radius: 50%;
  animation: apl-spin 0.9s linear infinite;
}
@keyframes apl-spin { to { transform: rotate(360deg); } }
/* #endregion */

/* Topbar button active state (drawer Runs toggle) */
.apl-btn-active {
  background: rgba(94, 106, 210, 0.12);
  color: var(--apl-blue);
  border: 0.5px solid rgba(94, 106, 210, 0.3);
}
.apl-btn-active:hover { background: rgba(94, 106, 210, 0.18); }

/* Badge count bên trong topbar button */
.apl-btn-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  min-width: 18px;
  height: 18px;
  margin-left: 4px;
  padding: 0 5px;
  background: rgba(118, 118, 128, 0.18);
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.apl-btn-active .apl-btn-badge { background: rgba(94, 106, 210, 0.2); color: var(--apl-blue); }
.apl-badge-running {
  background: rgba(255,149,0,0.15) !important;
  color: #A86200 !important;
}
.apl-badge-running .bi { font-size: 8px; }

/* Triggers list */
.apl-triggers { display: flex; flex-direction: column; gap: 4px; }
.apl-trigger-row {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 8px;
  background: var(--apl-bg-secondary);
  border: 0.5px solid var(--apl-separator);
  border-radius: 6px;
  font-size: 11px;
}
.apl-trigger-source {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 9.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.04em;
}
.apl-trigger-source.src-session { background: rgba(52,199,89,0.15); color: #7CDDAA; }
.apl-trigger-source.src-url     { background: rgba(10,132,255,0.15); color: #8FBAF0; }
.apl-trigger-source.src-static  { background: rgba(175,82,222,0.16); color: #702A98; }
.apl-trigger-type {
  flex-shrink: 0;
  font-size: 9.5px;
  color: var(--apl-label-2);
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.apl-trigger-detail {
  flex: 1; min-width: 0;
  font-family: ui-monospace, SFMono-Regular, monospace;
  color: var(--apl-label);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-size: 10.5px;
}
/* Event log — Apple timeline style: dot + msg + relative time */
.apl-event-count {
  display: inline-flex; align-items: center;
  padding: 1px 6px;
  background: rgba(118, 118, 128, 0.16);
  color: var(--apl-label-2);
  border-radius: 999px;
  font-size: 9.5px; font-weight: 700;
  font-family: ui-monospace, SFMono-Regular, monospace;
}
.apl-events {
  display: flex; flex-direction: column;
  background: var(--apl-bg-secondary);
  border: 0.5px solid var(--apl-separator);
  border-radius: 8px;
  overflow: hidden;
  /* Không set max-height → để .apl-detail-body lo scroll (1 scroll only) */
}
.apl-event {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px;
  border-bottom: 0.5px solid rgba(235, 236, 240, 0.06);
  font-size: 11px;
  transition: background 0.12s;
}
.apl-event:last-child { border-bottom: none; }
.apl-event:hover { background: rgba(118, 118, 128, 0.04); }
.apl-event-dot {
  flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  width: 14px; height: 14px;
  border-radius: 50%;
  background: rgba(94, 106, 210, 0.15);
  color: var(--apl-blue);
  font-size: 8px;
}
.dot-info    { background: rgba(94, 106, 210, 0.15);  color: #5E6AD2; }
.dot-success { background: rgba(52, 199, 89, 0.18);  color: #7CDDAA; }
.dot-warn    { background: rgba(255, 149, 0, 0.18);  color: #A86200; }
.dot-error   { background: rgba(255, 59, 48, 0.18);  color: #F29B9B; }
.apl-event-msg {
  flex: 1; min-width: 0;
  color: var(--apl-label);
  word-break: break-word;
  line-height: 1.4;
}
.apl-event-ts {
  flex-shrink: 0;
  font-size: 9.5px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  color: var(--apl-label-3);
  font-variant-numeric: tabular-nums;
}

/* Inspector */
.apl-inspector-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 16px 10px;
  border-bottom: 0.5px solid var(--apl-separator);
  background: rgba(20, 20, 24, 0.88);
  backdrop-filter: blur(30px) saturate(180%);
  -webkit-backdrop-filter: blur(30px) saturate(180%);
  position: sticky;
  top: 0;
  z-index: 10;
}
.apl-inspector-icon {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
}
.apl-inspector-overline { font-size: 10px; font-weight: 700; color: var(--apl-label-2); text-transform: uppercase; letter-spacing: 0.06em; }
.apl-inspector-title    { font-size: 15px; font-weight: 600; color: var(--apl-label); letter-spacing: -0.02em; }

.apl-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  border-radius: 8px;
  color: var(--apl-label-2);
  cursor: pointer;
  transition: all 0.15s;
}
.apl-icon-btn:hover { background: rgba(118, 118, 128, 0.12); }
.apl-icon-btn-danger { color: #FF3B30; }
.apl-icon-btn-danger:hover { background: rgba(255,59,48,0.15); }

.apl-inspector-body { padding: 14px 18px 24px; display: flex; flex-direction: column; gap: 14px; }

.apl-id-chip {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: rgba(118, 118, 128, 0.08);
  border-radius: 8px;
}
.apl-id-code { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 11px; color: var(--apl-label); word-break: break-all; }

/* Test Run modal */
.apl-modal-backdrop {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(8px) saturate(140%);
  -webkit-backdrop-filter: blur(8px) saturate(140%);
  display: flex; align-items: center; justify-content: center;
  padding: 20px;
}
.apl-modal {
  width: 100%; max-width: 480px;
  /* Flex column để body scroll bên trong; max-height set qua Tailwind
     utility (vd max-h-11/12) trên markup mỗi modal cho phù hợp content. */
  display: flex; flex-direction: column;
  background: rgba(24, 24, 28, 0.96);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border-radius: 18px;
  border: 0.5px solid rgba(235, 236, 240, 0.12);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.2), 0 8px 24px rgba(0, 0, 0, 0.12);
  overflow: hidden;
}
.apl-modal-wide { max-width: 680px; }
.apl-modal-header, .apl-modal-footer { flex-shrink: 0; }
.apl-curl-pre {
  background: var(--apl-fill-2);
  color: #f5f5f7;
  padding: 14px 16px;
  border-radius: 10px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow-y: auto;
  margin: 0;
}
.apl-modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 18px;
  border-bottom: 0.5px solid var(--apl-separator);
}
.apl-modal-icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: 10px;
  background: rgba(94, 106, 210, 0.12); color: var(--apl-blue);
  font-size: 16px;
}
.apl-modal-overline { font-size: 10px; font-weight: 700; color: var(--apl-label-2); text-transform: uppercase; letter-spacing: 0.06em; }
.apl-modal-title    { font-size: 15px; font-weight: 600; color: var(--apl-label); letter-spacing: -0.01em; font-family: ui-monospace, SFMono-Regular, monospace; }
.apl-icon-btn-modal {
  display: inline-flex; align-items: center; justify-content: center;
  width: 30px; height: 30px;
  background: transparent; border: none; border-radius: 8px;
  color: var(--apl-label-2); cursor: pointer; transition: all 0.15s;
}
.apl-icon-btn-modal:hover { background: rgba(118, 118, 128, 0.12); color: var(--apl-label); }

.apl-modal-body {
  padding: 16px 18px;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}
.apl-modal-label { font-size: 11px; font-weight: 700; color: var(--apl-label-2); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-modal-input {
  display: block; width: 100%;
  padding: 10px 12px;
  background: var(--apl-bg-secondary);
  border: 0.5px solid var(--apl-separator);
  border-radius: 10px;
  font-size: 14px; color: var(--apl-label);
  outline: none; transition: all 0.15s;
  font-family: inherit; resize: vertical;
}
.apl-input-list { display: flex; flex-direction: column; gap: 4px; }
.apl-input-row {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 8px;
  background: rgba(118,118,128,0.08);
  border-radius: 6px;
  font-size: 11px;
}
.apl-input-badge {
  font-family: ui-monospace, SFMono-Regular, monospace;
  color: var(--apl-blue); font-weight: 600;
}
.apl-input-type {
  margin-left: auto;
  padding: 1px 6px;
  background: var(--apl-bg-secondary);
  border: 0.5px solid var(--apl-separator);
  border-radius: 4px;
  font-size: 9.5px; font-weight: 700;
  text-transform: uppercase;
  color: var(--apl-label-2);
}
.apl-modal-input:focus { border-color: var(--apl-blue); box-shadow: 0 0 0 3px rgba(94, 106, 210, 0.2); }
.apl-modal-hint { font-size: 11px; color: var(--apl-label-2); margin-top: 8px; line-height: 1.4; }
.apl-kbd-mini {
  display: inline-block; padding: 0 4px;
  background: var(--apl-bg-secondary); border: 0.5px solid var(--apl-separator);
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 10px; font-weight: 600; color: var(--apl-label);
}
.apl-modal-footer {
  display: flex; align-items: center; justify-content: flex-end; gap: 8px;
  padding: 14px 18px;
  background: rgba(118, 118, 128, 0.06);
  border-top: 0.5px solid var(--apl-separator);
}

/* #region ALD 22/05/2026 - API modal (Sync + Async) styles */
.apl-modal-xl { max-width: 880px; }

.apl-api-body {
  display: flex; flex-direction: column; gap: 14px;
}
.apl-api-section {
  border: 0.5px solid var(--apl-separator);
  border-radius: 12px;
  background: var(--apl-bg-secondary);
  padding: 14px;
}
.apl-api-section-async {
  background: rgba(0, 49, 167, 0.025);
  border-color: rgba(0, 49, 167, 0.14);
}
.apl-api-section-head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 8px;
  margin-bottom: 8px;
}
.apl-api-section-title {
  font-size: 13px; font-weight: 700; color: #F7F8F8; letter-spacing: -0.01em;
}
.apl-api-section-sub {
  margin-top: 2px;
  font-size: 11px; color: var(--apl-label-2); line-height: 1.4;
}
.apl-api-subhead {
  font-size: 10px; font-weight: 700;
  color: var(--apl-label);
  text-transform: uppercase; letter-spacing: 0.06em;
  margin-top: 14px; margin-bottom: 8px;
  padding-top: 10px;
  border-top: 0.5px dashed rgba(235, 236, 240, 0.12);
}
.apl-api-subhead:first-child {
  margin-top: 12px; padding-top: 0; border-top: none;
}

.apl-async-toggle {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 0;
  background: transparent;
  border: none;
  border-radius: 0;
  cursor: pointer;
}
.apl-checkbox {
  width: 16px; height: 16px;
  margin-top: 2px;
  accent-color: #5E6AD2;
  cursor: pointer;
  flex-shrink: 0;
}
.apl-async-toggle-label { display: block; font-size: 13px; font-weight: 700; color: #F7F8F8; letter-spacing: -0.01em; }
.apl-async-toggle-hint { display: block; margin-top: 2px; font-size: 11.5px; color: var(--apl-label-2); line-height: 1.45; }
.apl-async-toggle-hint code {
  background: rgba(0,49,167,0.08); color: #9AA2F2;
  padding: 1px 5px; border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, monospace; font-size: 10.5px;
  font-weight: 600;
}
.apl-async-body { padding-top: 4px; }

.apl-header-list { display: flex; flex-direction: column; gap: 6px; }
.apl-header-row {
  display: flex; align-items: center; gap: 6px;
}
.apl-header-key { flex: 0 0 200px; }
.apl-header-val { flex: 1 1 auto; min-width: 0; }
.apl-btn-mini {
  padding: 5px 10px !important;
  font-size: 11.5px !important;
}

/* Preview grid: 2-col trên desktop, 1-col mobile */
.apl-preview-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}
@media (min-width: 820px) {
  .apl-preview-grid { grid-template-columns: 1fr 1fr; }
}
.apl-preview-block {
  display: flex; flex-direction: column;
  min-width: 0;  /* allow pre to shrink for overflow */
}
.apl-preview-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 6px;
  margin-bottom: 4px;
}
.apl-preview-title {
  font-size: 10px; font-weight: 700;
  color: var(--apl-label);
  text-transform: uppercase; letter-spacing: 0.04em;
}

.apl-icon-btn-copy {
  width: 26px; height: 26px;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--apl-bg-secondary);
  border: 0.5px solid var(--apl-separator);
  border-radius: 6px;
  color: var(--apl-label-2);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.12s;
  flex-shrink: 0;
}
.apl-icon-btn-copy:hover {
  border-color: var(--apl-blue);
  color: var(--apl-blue);
  background: rgba(94, 106, 210, 0.05);
}
/* #endregion */

/* On Failure section */
.apl-on-failure {
  margin-top: 4px;
  padding: 12px;
  background: rgba(255, 149, 0, 0.06);
  border: 0.5px solid rgba(255, 149, 0, 0.2);
  border-radius: 10px;
}
.apl-of-label { display: block; font-size: 11px; font-weight: 700; color: #A86200; text-transform: uppercase; letter-spacing: 0.04em; }
.apl-of-select {
  display: block; width: 100%;
  padding: 7px 10px; background: var(--apl-bg-secondary);
  border: 0.5px solid rgba(235, 236, 240, 0.18); border-radius: 8px;
  font-size: 13px; color: var(--apl-label); outline: none;
  font-family: inherit; cursor: pointer;
}
.apl-of-select:focus { border-color: #FF9500; box-shadow: 0 0 0 3px rgba(255, 149, 0, 0.2); }
.apl-of-hint { margin-top: 6px; font-size: 11px; color: var(--apl-label); line-height: 1.4; }

/* Vue Flow overrides */
.vue-flow__node { padding: 0 !important; background: transparent !important; border: none !important; box-shadow: none !important; }
.vue-flow__node-step { color: inherit; }
/* ALD 24/05/2026 - Selected node: ring System Blue mềm + tăng nhẹ scale. Trước default
   Vue Flow outline dashed gray vuông góc khá xấu — thay bằng glow tròn theo Apple HIG. */
.vue-flow__node.selected {
  outline: none !important;
  box-shadow: none !important;
}
.vue-flow__node.selected > * {
  box-shadow:
    0 0 0 2px rgba(94,106,210,0.55),
    0 0 0 6px rgba(94,106,210,0.16),
    0 12px 32px rgba(94,106,210,0.18) !important;
  transition: box-shadow 0.18s ease, transform 0.18s ease;
}
/* Multi-select indicator (rubber band drag) */
.vue-flow__selection {
  background: rgba(94,106,210,0.07) !important;
  border: 1.5px dashed rgba(94,106,210,0.55) !important;
  border-radius: 12px !important;
}
/* Bounding box quanh group nodes đã select (Vue Flow render nodesselection-rect) */
.vue-flow__nodesselection-rect {
  background: transparent !important;
  border: 1.5px dashed rgba(94,106,210,0.45) !important;
  border-radius: 14px !important;
  box-shadow: 0 0 0 4px rgba(94,106,210,0.08) inset !important;
}

.vue-flow__edge-text { font-size: 10px !important; font-weight: 700 !important; }
.vue-flow__edge-textbg { fill: white; }
/* Default edge — soft slate, slightly thicker, smooth bezier */
.vue-flow__edge-path {
  stroke: #94a3b8;
  stroke-width: 2;
  transition: stroke 0.18s ease, stroke-width 0.18s ease;
}
.vue-flow__edge:hover .vue-flow__edge-path {
  stroke: #475569;
  stroke-width: 2.5;
}
.vue-flow__edge.selected .vue-flow__edge-path {
  stroke: #5E6AD2 !important;
  stroke-width: 2.5 !important;
}
/* Connection-line (dragging from handle) */
.vue-flow__connectionline { stroke: #5E6AD2; stroke-width: 2.5; stroke-dasharray: 5 5; }

/* Special edges */
.vue-flow__edge.edge-true    .vue-flow__edge-path,
.vue-flow__edge.edge-success .vue-flow__edge-path { stroke: #34C759 !important; stroke-width: 2 !important; }
.vue-flow__edge.edge-false   .vue-flow__edge-path { stroke: #FF3B30 !important; stroke-width: 2 !important; }
.vue-flow__edge.edge-error   .vue-flow__edge-path { stroke: #FF9500 !important; stroke-width: 2 !important; stroke-dasharray: 6 4; }
.vue-flow__edge.edge-true    .vue-flow__edge-text,
.vue-flow__edge.edge-success .vue-flow__edge-text { fill: #7CDDAA !important; }
.vue-flow__edge.edge-false   .vue-flow__edge-text { fill: #F29B9B !important; }
.vue-flow__edge.edge-error   .vue-flow__edge-text { fill: #F5D77A !important; }

.vue-flow__controls {
  background: rgba(24, 24, 28, 0.92) !important;
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border-radius: 12px !important;
  border: 0.5px solid var(--apl-separator) !important;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08) !important;
  margin: 16px !important;
  overflow: hidden;
}
.vue-flow__controls button {
  background: transparent !important;
  border: none !important;
  border-bottom: 0.5px solid var(--apl-separator) !important;
  color: var(--apl-label) !important;
}
.vue-flow__controls button:last-child { border-bottom: none !important; }
.vue-flow__controls button svg { fill: #C9CDD1 !important; }
.vue-flow__controls button:hover { background: var(--apl-fill-2) !important; }

.vue-flow__minimap {
  background: rgba(24, 24, 28, 0.92) !important;
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 0.5px solid var(--apl-separator) !important;
  border-radius: 12px !important;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08) !important;
  margin: 16px !important;
}

/* ── ALD 06/07/2026 - LIGHT SKIN cho editor (class theme-light trên <html>) ── */
html.theme-light {
  --apl-bg:           #F2F2F7;
  --apl-bg-secondary: #FFFFFF;
  --apl-separator:    rgba(0, 0, 0, 0.1);
  --apl-label:        #1D1D1F;
  --apl-label-2:      rgba(60, 60, 67, 0.6);
  --apl-label-3:      rgba(60, 60, 67, 0.3);
  --apl-fill:         rgba(0, 0, 0, 0.045);
  --apl-fill-2:       rgba(0, 0, 0, 0.08);
  --apl-node-bg:            rgba(255, 255, 255, 0.92);
  --apl-node-border:        rgba(0, 0, 0, 0.1);
  --apl-node-border-hover:  rgba(0, 0, 0, 0.22);
  --apl-node-shadow:        0 1px 2px rgba(0, 0, 0, 0.06), 0 12px 28px -8px rgba(0, 0, 0, 0.12);
  --apl-node-shadow-hover:  0 2px 4px rgba(0, 0, 0, 0.08), 0 18px 40px -8px rgba(0, 0, 0, 0.18);
}
html.theme-light .apl-sidebar { background: rgba(255, 255, 255, 0.72); }
html.theme-light .apl-topbar { background: rgba(255, 255, 255, 0.72); }
html.theme-light .apl-palette-item { background: #FFFFFF; }
html.theme-light .apl-palette-item:hover { background: #FAFAFC; border-color: rgba(0, 0, 0, 0.18); }
html.theme-light .apl-search-input { background: rgba(118, 118, 128, 0.12); color: var(--apl-label); }
html.theme-light .apl-search-input:focus { background: #FFFFFF; }
html.theme-light .apl-kbd { background: #FFFFFF; color: var(--apl-label); }
html.theme-light .apl-runtab { background: #F4F4F8; border-color: #E4E4EC; color: #6B6B78; }
html.theme-light .apl-runtab:hover { background: #ECECF2; }
html.theme-light .apl-runtab.is-active { background: #FFFFFF; color: #1D1D28; }
html.theme-light .apl-canvas::before {
  background-image:
    linear-gradient(rgba(0, 0, 0, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 0, 0, 0.05) 1px, transparent 1px);
}
html.theme-light .apl-canvas::after {
  background-image:
    linear-gradient(rgba(94, 106, 210, 0.12) 1px, transparent 1px),
    linear-gradient(90deg, rgba(94, 106, 210, 0.12) 1px, transparent 1px);
}
html.theme-light .vue-flow__controls,
html.theme-light .vue-flow__minimap { background: rgba(255, 255, 255, 0.92) !important; }
html.theme-light .vue-flow__controls button svg { fill: #48484A !important; }
html.theme-light .vue-flow__controls button:hover { background: rgba(0, 0, 0, 0.05) !important; }
/* Các mảng glass tối còn hardcode → sáng lại khi theme-light */
html.theme-light .apl-inspector-header { background: rgba(255, 255, 255, 0.85); }
html.theme-light .apl-run-drawer { background: rgba(255, 255, 255, 0.85); }
html.theme-light .apl-modal {
  background: rgba(255, 255, 255, 0.97);
  border-color: rgba(0, 0, 0, 0.1);
}
html.theme-light .apl-icon-btn:hover { background: rgba(0, 0, 0, 0.06); }
html.theme-light .apl-pill-success { background: rgba(52,199,89,0.14); color: #1F7D38; }
html.theme-light .apl-pill-error { background: rgba(255,59,48,0.12); color: #C0261F; }
html.theme-light .apl-trigger-source.src-session { background: rgba(52,199,89,0.14); color: #1F7D38; }
html.theme-light .apl-trigger-source.src-url { background: rgba(10,132,255,0.12); color: #0050B5; }
html.theme-light .dot-success { color: #1F7D38; }
html.theme-light .apl-curl-pre { background: #1D1D1F; }
/* #endregion */
</style>
