// Tiny fetch wrapper that understands the backend's unified envelope:
//   success: { success:true, data, message }
//   failure: { success:false, code, message, detail }
// Throws an Error (with .code/.detail) on failure so callers can try/catch.

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* non-JSON */
  }
  if (!res.ok || payload.success === false) {
    const err = new Error(payload.message || `Request failed (${res.status})`);
    err.code = payload.code || "HTTP_ERROR";
    err.detail = payload.detail || {};
    throw err;
  }
  return payload.data;
}

// Multipart upload (e.g. .ipa). No Content-Type header → the browser sets the
// multipart boundary itself.
async function postForm(path, formData) {
  const res = await fetch(path, { method: "POST", body: formData });
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    /* non-JSON */
  }
  if (!res.ok || payload.success === false) {
    const err = new Error(payload.message || `Request failed (${res.status})`);
    err.code = payload.code || "HTTP_ERROR";
    err.detail = payload.detail || {};
    throw err;
  }
  return payload.data;
}

export const http = {
  get: (p) => request("GET", p),
  post: (p, body) => request("POST", p, body),
  put: (p, body) => request("PUT", p, body),
  del: (p) => request("DELETE", p),
  postForm,
};
