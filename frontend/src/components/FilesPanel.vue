<template>
  <div class="card">
    <div class="row" style="justify-content: space-between; gap: 8px">
      <strong>{{ t("files.title") }}</strong>
      <button class="ghost-sm" :disabled="loading" @click="list">{{ loading ? "…" : t("files.refresh") }}</button>
    </div>

    <!-- Path / root selector -->
    <div class="block">
      <label class="muted">{{ t("files.mediaRoot") }}</label>
      <div class="row">
        <input v-model="path" style="flex: 1; min-width: 0" :placeholder="t('files.pathPlaceholder')" @keyup.enter="list" />
        <button @click="list" :disabled="loading">{{ t("files.list") }}</button>
      </div>
    </div>

    <p v-if="error" class="banner err">{{ error }}</p>

    <!-- Interactive file tree: folders expand/collapse, files download on click. -->
    <template v-if="nodes.length">
      <div class="row" style="justify-content: space-between; margin-top: 10px">
        <span class="muted browse-hint">{{ t("files.browseHint") }}</span>
        <button v-if="anyExpanded" class="link-btn" @click="collapseAll">{{ t("files.collapseAll") }}</button>
      </div>
      <div class="tree">
        <div
          v-for="row in visibleRows"
          :key="row.node.path"
          class="tree-row"
          :class="{ dir: row.node.isDir }"
          :style="{ paddingLeft: 6 + row.depth * 14 + 'px' }"
          @click="onRow(row.node)"
        >
          <span class="twist">{{ row.node.isDir ? (row.node.expanded ? "▾" : "▸") : "" }}</span>
          <svg v-if="row.node.isDir" class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
          <svg v-else class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>
          <span class="tname">{{ row.node.name }}</span>
          <span v-if="!row.node.isDir" class="rowacts">
            <svg v-if="canPreview(row.node.name)" class="act" :title="t('files.preview')" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" @click.stop="previewNode(row.node)"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>
            <svg class="act" :title="t('files.download')" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" @click.stop="downloadNode(row.node)"><path d="M12 3v12"/><path d="m7 12 5 5 5-5"/><path d="M5 21h14"/></svg>
          </span>
        </div>
      </div>
    </template>
    <p v-else-if="listed && !loading" class="muted" style="margin-top: 10px; font-size: 0.8rem">{{ t("files.empty") }}</p>

    <!-- Upload — target defaults to the directory currently being browsed. -->
    <div class="block">
      <label class="muted">{{ t("files.uploadLabel") }}</label>
      <div class="row">
        <input v-model="upPath" style="flex: 1; min-width: 0" :placeholder="t('files.uploadPlaceholder')" />
        <button :disabled="uploading" @click="$refs.file.click()">
          {{ uploading ? t("files.uploading") : t("files.chooseFile") }}
        </button>
        <input ref="file" type="file" style="display: none" @change="onPick" />
      </div>
    </div>

    <!-- In-app preview overlay (close via ✕ / backdrop / Esc). -->
    <Teleport to="body">
      <transition name="fmodal">
        <div v-if="preview" class="filemodal" @click.self="closePreview">
          <div class="filemodal-box">
            <header class="filemodal-head">
              <span class="filemodal-name" :title="preview.name">{{ preview.name }}</span>
              <a class="filemodal-act" :href="preview.dlUrl" :download="preview.name" :title="t('files.download')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 12 5 5 5-5"/><path d="M5 21h14"/></svg>
              </a>
              <button class="filemodal-act" :title="t('detail.close')" @click="closePreview">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>
              </button>
            </header>
            <div class="filemodal-body">
              <img v-if="preview.kind === 'image'" :src="preview.url" :alt="preview.name" />
              <video v-else-if="preview.kind === 'video'" :src="preview.url" controls autoplay />
              <audio v-else-if="preview.kind === 'audio'" :src="preview.url" controls autoplay />
              <iframe v-else :src="preview.url" :title="preview.name" />
            </div>
          </div>
        </div>
      </transition>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { filesApi } from "../api/files";
import { useUiI18n } from "../composables/useUiI18n";
import { useToast } from "../composables/useToast";

const props = defineProps({ udid: { type: String, required: true } });
const { t } = useUiI18n();
const { push: toast } = useToast();

const path = ref(".");
const nodes = ref([]); // parsed tree (top-level entries)
const listed = ref(false);
const loading = ref(false);
const error = ref("");
const upPath = ref("Downloads"); // a sensible writable default in the media root
const uploading = ref(false);

// Extensions browsers can render inline (so we only show the preview eye for
// these). HEIC/HEIF are excluded — no native browser support.
const KIND = {
  image: new Set(["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "avif", "ico"]),
  video: new Set(["mp4", "webm", "ogv", "mov", "m4v"]),
  audio: new Set(["mp3", "wav", "m4a", "aac", "oga", "ogg", "flac"]),
  doc: new Set(["txt", "json", "xml", "log", "pdf"]),
};
function kindOf(name) {
  const ext = name.includes(".") ? name.split(".").pop().toLowerCase() : "";
  for (const k of Object.keys(KIND)) if (KIND[k].has(ext)) return k;
  return null;
}
function canPreview(name) {
  return kindOf(name) !== null;
}

// In-app preview overlay (NOT window.open — that navigated away with no way back
// and offered no close affordance).
const preview = ref(null); // { name, url, dlUrl, kind }
function previewNode(node) {
  preview.value = {
    name: node.name,
    url: filesApi.previewUrl(props.udid, node.path),
    dlUrl: filesApi.pullUrl(props.udid, node.path),
    kind: kindOf(node.name),
  };
}
function closePreview() { preview.value = null; }
function onKey(e) { if (e.key === "Escape") closePreview(); }
onMounted(() => window.addEventListener("keydown", onKey));
onUnmounted(() => window.removeEventListener("keydown", onKey));

// go-ios `fsync tree` returns a flat indented text tree:
//   |-Books/
//   |  |-Managed/
//   |  |  |-.Managed.plist.lock
// Depth = column of "|-" / 3; dirs end with "/". Parse into a nested structure.
function parseTree(text, basePath) {
  const base = basePath && basePath !== "." ? basePath.replace(/\/+$/, "") : "";
  const roots = [];
  const stack = [{ children: roots, depth: -1, path: base }];
  for (const raw of text.split("\n")) {
    const idx = raw.indexOf("|-");
    if (idx < 0) continue;
    const depth = Math.floor(idx / 3);
    let name = raw.slice(idx + 2);
    if (!name) continue;
    const isDir = name.endsWith("/");
    if (isDir) name = name.slice(0, -1);
    while (stack.length > 1 && stack[stack.length - 1].depth >= depth) stack.pop();
    const parent = stack[stack.length - 1];
    const node = {
      name,
      isDir,
      expanded: false,
      children: [],
      path: (parent.path ? parent.path + "/" : "") + name,
    };
    parent.children.push(node);
    if (isDir) stack.push({ children: node.children, depth, path: node.path });
  }
  return roots;
}

// Flatten the tree into the rows currently visible (respecting expand state).
const visibleRows = computed(() => {
  const out = [];
  const walk = (list, depth) => {
    for (const node of list) {
      out.push({ node, depth });
      if (node.isDir && node.expanded && node.children.length) walk(node.children, depth + 1);
    }
  };
  walk(nodes.value, 0);
  return out;
});
const anyExpanded = computed(() => {
  let found = false;
  const walk = (l) => l.forEach((n) => { if (n.expanded) found = true; if (n.children) walk(n.children); });
  walk(nodes.value);
  return found;
});

async function list() {
  loading.value = true;
  error.value = "";
  try {
    const d = await filesApi.tree(props.udid, path.value || ".");
    nodes.value = d.tree ? parseTree(d.tree, path.value) : [];
    listed.value = true;
    // Link the upload target to the browsed sub-directory; keep the default for root.
    if (path.value && path.value !== ".") upPath.value = path.value.replace(/\/+$/, "");
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}

function onRow(node) {
  if (node.isDir) node.expanded = !node.expanded;
  else downloadNode(node);
}

function collapseAll() {
  const walk = (l) => l.forEach((n) => { n.expanded = false; if (n.children) walk(n.children); });
  walk(nodes.value);
}

function downloadNode(node) {
  const a = document.createElement("a");
  a.href = filesApi.pullUrl(props.udid, node.path);
  a.download = "";
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast(t("files.downloadDone", { name: node.name }));
}

async function onPick(ev) {
  const file = ev.target.files?.[0];
  ev.target.value = "";
  if (!file) return;
  // Upload into the browsed directory (target = dir + filename) when a dir is set.
  const dir = (upPath.value || "").replace(/\/+$/, "");
  const dst = dir ? `${dir}/${file.name}` : file.name;
  uploading.value = true;
  error.value = "";
  try {
    await filesApi.push(props.udid, file, dst);
    await list();
  } catch (e) {
    error.value = e.message;
  } finally {
    uploading.value = false;
  }
}
</script>

<style scoped>
.block { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.browse-hint { font-size: 0.72rem; }
.ghost-sm { min-height: 32px; padding: 4px 10px; font-size: 0.8rem; }
.link-btn { min-height: 0; padding: 0; border: none; background: transparent; color: var(--primary); font-size: 0.72rem; }
.tree {
  margin-top: 6px; max-height: 280px; overflow: auto;
  background: var(--inset); border: 1px solid var(--border); border-radius: 8px; padding: 4px;
}
.tree-row {
  display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-radius: 6px;
  font-size: 0.78rem; cursor: pointer; white-space: nowrap;
}
.tree-row:hover { background: var(--surface-2); }
.tree-row .twist { width: 12px; flex: 0 0 auto; color: var(--muted); font-size: 0.7rem; text-align: center; }
.tree-row .ic { width: 15px; height: 15px; flex: 0 0 auto; color: var(--muted); }
.tree-row.dir .ic { color: var(--primary); }
.tree-row .tname { overflow: hidden; text-overflow: ellipsis; }
.tree-row .rowacts { margin-left: auto; display: inline-flex; align-items: center; gap: 8px; flex: 0 0 auto; opacity: 0; }
.tree-row:hover .rowacts, .tree-row:focus-within .rowacts { opacity: 1; }
.tree-row .act { width: 15px; height: 15px; color: var(--muted); cursor: pointer; }
.tree-row .act:hover { color: var(--primary); }

/* Preview overlay */
.filemodal {
  position: fixed; inset: 0; z-index: 1000; display: grid; place-items: center; padding: 24px;
  background: rgba(2, 6, 23, 0.66); backdrop-filter: blur(3px);
}
.filemodal-box {
  display: flex; flex-direction: column; max-width: min(92vw, 1100px); max-height: 88vh;
  background: var(--panel-solid); border: 1px solid var(--border-strong);
  border-radius: 14px; box-shadow: var(--shadow); overflow: hidden;
}
.filemodal-head {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px;
  border-bottom: 1px solid var(--border);
}
.filemodal-name {
  flex: 1; min-width: 0; font-size: 0.84rem; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.filemodal-act {
  flex: 0 0 auto; width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center;
  border-radius: 8px; border: 1px solid var(--border-strong); background: var(--panel); color: var(--muted); cursor: pointer;
}
.filemodal-act:hover { color: var(--text-strong); border-color: var(--primary-2); }
.filemodal-act svg { width: 16px; height: 16px; }
.filemodal-body { display: grid; place-items: center; overflow: auto; padding: 12px; background: var(--inset); }
.filemodal-body img, .filemodal-body video { max-width: 100%; max-height: calc(88vh - 120px); border-radius: 8px; display: block; }
.filemodal-body audio { width: min(80vw, 460px); }
.filemodal-body iframe { width: min(88vw, 900px); height: calc(88vh - 120px); border: none; background: #fff; border-radius: 8px; }
.fmodal-enter-active, .fmodal-leave-active { transition: opacity 0.18s ease; }
.fmodal-enter-from, .fmodal-leave-to { opacity: 0; }
</style>
