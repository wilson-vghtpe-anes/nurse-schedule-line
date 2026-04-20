const LIFF_ID = window.__LIFF_ID__ || "";
const API_BASE = window.__API_BASE__ || "";

let _lineUserId = "";

async function initLiff(requiredRole) {
  await liff.init({ liffId: LIFF_ID });
  if (!liff.isLoggedIn()) {
    liff.login();
    return;
  }
  const profile = await liff.getProfile();
  _lineUserId = profile.userId;

  if (requiredRole) {
    try {
      const me = await apiFetch("/api/me");
      const roleRank = { nurse: 1, manager: 2, admin: 3 };
      if ((roleRank[me.role] || 0) < (roleRank[requiredRole] || 0)) {
        document.body.innerHTML = '<p style="padding:24px;color:#c00">您沒有此頁面的存取權限。</p>';
        return null;
      }
      return me;
    } catch (e) {
      if (e.status === 403) {
        window.location.href = "/index.html";
      }
      return null;
    }
  }
  return null;
}

async function apiFetch(path, opts = {}) {
  const res = await fetch(API_BASE + path, {
    ...opts,
    headers: {
      "X-Line-User-Id": _lineUserId,
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    const err = new Error(await res.text());
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function formatShift(shift_type) {
  return shift_type || "";
}

function formatDate(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr + "T00:00:00");
  const days = ["日", "一", "二", "三", "四", "五", "六"];
  return `${dateStr}（${days[d.getDay()]}）`;
}

function statusLabel(status) {
  const map = {
    submitted: "已提交",
    pending_peer: "等待對方確認",
    pending_admin: "等待主管審核",
    approved: "已核准",
    rejected: "已拒絕",
    cancelled: "已取消",
    conflict_rejected: "因衝突自動取消",
    peer_rejected: "對方已拒絕",
    manager_direct: "主管直接調班",
  };
  return map[status] || status;
}

function statusBadge(status) {
  const cls = {
    approved: "badge-green",
    pending_admin: "badge-orange",
    pending_peer: "badge-blue",
    rejected: "badge-red",
    cancelled: "badge-gray",
    conflict_rejected: "badge-gray",
    peer_rejected: "badge-red",
  }[status] || "badge-gray";
  return `<span class="badge ${cls}">${statusLabel(status)}</span>`;
}

function showToast(msg, ok = true) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.className = "toast " + (ok ? "toast-ok" : "toast-err");
  el.style.display = "block";
  setTimeout(() => { el.style.display = "none"; }, 2800);
}

function loading(show) {
  const el = document.getElementById("loading");
  if (el) el.style.display = show ? "flex" : "none";
}

function showConfirm(msg, { okLabel = "確認", cancelLabel = "取消" } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    overlay.innerHTML = `
      <div class="confirm-box">
        <p>${msg}</p>
        <div class="confirm-btns">
          <button class="btn btn-gray" id="_cancel">${cancelLabel}</button>
          <button class="btn btn-primary" id="_ok">${okLabel}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector("#_ok").onclick = () => { overlay.remove(); resolve(true); };
    overlay.querySelector("#_cancel").onclick = () => { overlay.remove(); resolve(false); };
  });
}

function today() {
  return new Date().toISOString().slice(0, 10);
}
