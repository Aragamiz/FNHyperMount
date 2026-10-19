/**
 * FNHyperMount - Frontend App Logic
 * 负责状态自动轮询、各挂载点吞吐看板动态刷新与向导式挂载流程
 */

let currentMounts = [];
let healthCache = {};
let pollTimer = null;
let wizardCurrentStep = 1;
let wizardData = {
  server: "",
  export: "",
  mountpoint: "",
  preset: "automount",
  exportsList: []
};

// 页面加载入口
document.addEventListener("DOMContentLoaded", () => {
  initEventListeners();
  loadMounts();
  startPolling();
});

function initEventListeners() {
  document.getElementById("btn-refresh").addEventListener("click", () => {
    loadMounts();
  });

  // 打开/关闭向导
  document.getElementById("btn-open-wizard").addEventListener("click", openWizard);
  document.getElementById("btn-close-wizard").addEventListener("click", closeWizard);

  // 向导步骤导航
  document.getElementById("btn-wizard-prev").addEventListener("click", wizardPrevStep);
  document.getElementById("btn-wizard-next").addEventListener("click", wizardNextStep);
  document.getElementById("btn-wizard-finish").addEventListener("click", () => {
    closeWizard();
    loadMounts();
  });

  // 探测按钮
  document.getElementById("btn-do-probe").addEventListener("click", doServerProbe);

  // 挂载策略单选高亮
  document.querySelectorAll("input[name='wizard-preset']").forEach(radio => {
    radio.addEventListener("change", (e) => {
      document.querySelectorAll(".preset-card").forEach(c => c.classList.remove("selected"));
      e.target.closest(".preset-card").classList.add("selected");
      wizardData.preset = e.target.value;
    });
  });

  // 共享下拉框选择联动填充本地路径
  document.getElementById("wizard-export-select").addEventListener("change", (e) => {
    wizardData.export = e.target.value;
    const cleanName = e.target.value.replace(/[^a-zA-Z0-9_\-\u4e00-\u9fa5]/g, "_").replace(/^_+|_+$/g, "");
    const localInput = document.getElementById("wizard-local-mountpoint");
    if (!localInput.value || localInput.value.startsWith("/vol1/1000/")) {
      localInput.value = `/vol1/1000/NFS_${cleanName || "Share"}`;
      wizardData.mountpoint = localInput.value;
    }
  });

  // 体检 Modal 关闭
  document.getElementById("btn-close-health").addEventListener("click", closeHealthModal);
  document.getElementById("btn-health-ok").addEventListener("click", closeHealthModal);
}

// 自动轮询内核 mountstats
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    // 只有在没有打开向导模态框时才在后台静默轮询，防止干扰用户输入
    if (document.getElementById("wizard-modal").classList.contains("hidden")) {
      loadMounts(true);
    }
  }, 2000);
}

async function loadMounts(silent = false) {
  try {
    const res = await fetch("/api/mounts");
    const data = await res.json();
    if (data.ok) {
      currentMounts = data.mounts;
      renderOverviewStats(data.mounts);
      renderMountsList(data.mounts);
    }
  } catch (err) {
    if (!silent) console.error("加载挂载列表失败:", err);
  }
}

// 渲染顶部总体统计指标
function renderOverviewStats(mounts) {
  document.getElementById("stat-total-mounts").textContent = mounts.length;
  const onlineCount = mounts.filter(m => m.is_mounted).length;
  document.getElementById("stat-online-mounts").textContent = onlineCount;

  // 汇总总吞吐速率
  let totalReadBps = 0;
  let totalWriteBps = 0;
  mounts.forEach(m => {
    totalReadBps += (m.read_speed_bps || 0);
    totalWriteBps += (m.write_speed_bps || 0);
  });

  document.getElementById("stat-read-speed").textContent = formatSpeed(totalReadBps);
  document.getElementById("stat-write-speed").textContent = formatSpeed(totalWriteBps);
}

// 渲染挂载卡片列表
function renderMountsList(mounts) {
  const container = document.getElementById("mounts-container");
  if (!mounts || mounts.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p style="font-size:32px;margin-bottom:12px;">📁</p>
        <p>当前暂未配置任何 NFS 远程挂载点</p>
        <button class="btn btn-primary" style="margin-top:16px;" onclick="openWizard()">
          ➕ 立即通过向导添加第一个挂载
        </button>
      </div>
    `;
    return;
  }

  container.innerHTML = mounts.map(m => {
    const isOnline = m.is_mounted;
    const statusClass = isOnline ? "status-online" : "status-unmounted";
    const statusText = isOnline ? "🟢 正常在线 (已挂载)" : "⚪ 未挂载 (按需等待激活)";
    const percent = m.disk_percent || 0;

    return `
      <div class="mount-card" id="card-${m.id}">
        <div class="mount-card-header">
          <div class="mount-title-area">
            <span class="mount-folder-icon">📂</span>
            <div>
              <div class="mount-path">${escapeHtml(m.mountpoint)}</div>
              <div class="mount-device-tag">源自目标机器：${escapeHtml(m.device)}</div>
            </div>
          </div>
          <span class="status-badge ${statusClass}">${statusText}</span>
        </div>

        <!-- 核心指标网格：独立吞吐与存储空间 -->
        <div class="mount-metrics">
          <div class="metric-item">
            <span class="metric-label">实时读取速率</span>
            <span class="metric-val throughput-highlight">📥 ${m.read_speed_human || "0 B/s"}</span>
          </div>
          <div class="metric-item">
            <span class="metric-label">实时写入速率</span>
            <span class="metric-val throughput-highlight">📤 ${m.write_speed_human || "0 B/s"}</span>
          </div>
          <div class="metric-item">
            <span class="metric-label">累计读写吞吐</span>
            <span class="metric-val">读 ${m.read_bytes_human || "0 B"} / 写 ${m.write_bytes_human || "0 B"}</span>
          </div>
          <div class="metric-item">
            <span class="metric-label">存储容量 (${m.disk_used_human || "-"} / ${m.disk_total_human || "-"})</span>
            <span class="metric-val">已用 ${percent}% (剩余 ${m.disk_free_human || "-"})</span>
            <div class="progress-bar-container">
              <div class="progress-bar-fill" style="width: ${percent}%;"></div>
            </div>
          </div>
        </div>

        <div class="mount-card-footer">
          <!-- 用户红框区域：深度体检信息直接在此就地显示 -->
          <div class="health-inline-box" id="health-box-${m.id}">
            ${renderInlineHealth(m.mountpoint, m.server)}
          </div>

          <!-- 右侧操作按钮区 -->
          <div class="mount-card-actions">
            <button class="btn btn-secondary btn-sm" id="btn-probe-${m.id}" onclick="refreshLinkHealth('${escapeHtml(m.mountpoint)}', '${escapeHtml(m.server)}', '${m.id}')" title="立即重新测试对端延时、端口与真实读写落盘响应">
              🔄 刷新链接信息
            </button>
            <button class="btn btn-secondary btn-sm" onclick="triggerRemount('${escapeHtml(m.mountpoint)}')">
              ⚡ 重新挂载
            </button>
            <button class="btn btn-secondary btn-sm btn-danger-text" onclick="triggerRemove('${escapeHtml(m.mountpoint)}')">
              🗑️ 卸载并移除
            </button>
          </div>
        </div>
      </div>
    `;
  }).join("");

  // 渲染完成后，对未体检过的活跃挂载点自动触发一次后台静默刷新
  mounts.forEach(m => {
    if (m.is_mounted && !healthCache[m.mountpoint]) {
      setTimeout(() => refreshLinkHealth(m.mountpoint, m.server, m.id, true), 300);
    }
  });
}

// ==================== 向导逻辑 ====================

function openWizard() {
  wizardCurrentStep = 1;
  wizardData = { server: "", export: "", mountpoint: "", preset: "automount", exportsList: [] };
  document.getElementById("wizard-server-ip").value = "";
  document.getElementById("wizard-local-mountpoint").value = "";
  document.getElementById("probe-result-box").classList.add("hidden");
  document.getElementById("wizard-next-btn");
  updateWizardUI();
  document.getElementById("wizard-modal").classList.remove("hidden");
}

function closeWizard() {
  document.getElementById("wizard-modal").classList.add("hidden");
}

function updateWizardUI() {
  // 更新步骤条指示
  document.querySelectorAll(".step-item").forEach(item => {
    const s = parseInt(item.dataset.step);
    item.classList.toggle("active", s === wizardCurrentStep);
  });

  // 更新面板显隐
  for (let i = 1; i <= 4; i++) {
    const panel = document.getElementById(`step-panel-${i}`);
    if (panel) panel.classList.toggle("hidden", i !== wizardCurrentStep);
  }

  // 更新按钮状态
  const prevBtn = document.getElementById("btn-wizard-prev");
  const nextBtn = document.getElementById("btn-wizard-next");
  const finishBtn = document.getElementById("btn-wizard-finish");

  prevBtn.classList.toggle("hidden", wizardCurrentStep === 1 || wizardCurrentStep === 4);
  nextBtn.classList.toggle("hidden", wizardCurrentStep === 4);
  finishBtn.classList.toggle("hidden", wizardCurrentStep !== 4);

  if (wizardCurrentStep === 1) {
    nextBtn.disabled = !wizardData.exportsList || wizardData.exportsList.length === 0;
  } else if (wizardCurrentStep === 2) {
    nextBtn.disabled = !document.getElementById("wizard-local-mountpoint").value.trim();
  } else {
    nextBtn.disabled = false;
  }
}

function wizardPrevStep() {
  if (wizardCurrentStep > 1) {
    wizardCurrentStep--;
    updateWizardUI();
  }
}

async function wizardNextStep() {
  if (wizardCurrentStep === 1) {
    // 进入第 2 步，填充下拉选项
    const select = document.getElementById("wizard-export-select");
    select.innerHTML = wizardData.exportsList.map(exp => `
      <option value="${escapeHtml(exp.path)}">${escapeHtml(exp.path)} (权限: ${escapeHtml(exp.clients)})</option>
    `).join("");

    if (wizardData.exportsList.length > 0) {
      wizardData.export = wizardData.exportsList[0].path;
      const cleanName = wizardData.export.replace(/[^a-zA-Z0-9_\-\u4e00-\u9fa5]/g, "_").replace(/^_+|_+$/g, "");
      document.getElementById("wizard-local-mountpoint").value = `/vol1/1000/NFS_${cleanName || "Share"}`;
      wizardData.mountpoint = document.getElementById("wizard-local-mountpoint").value;
    }
    wizardCurrentStep = 2;
    updateWizardUI();
  } else if (wizardCurrentStep === 2) {
    wizardData.mountpoint = document.getElementById("wizard-local-mountpoint").value.trim();
    if (!wizardData.mountpoint) {
      alert("请填写飞牛本地挂载路径");
      return;
    }
    wizardCurrentStep = 3;
    updateWizardUI();
  } else if (wizardCurrentStep === 3) {
    // 进入第 4 步并执行挂载与验收
    wizardCurrentStep = 4;
    updateWizardUI();
    executeWizardMount();
  }
}

// 步骤 1 探测对端
async function doServerProbe() {
  const hostInput = document.getElementById("wizard-server-ip");
  const host = hostInput.value.trim();
  if (!host) {
    alert("请输入目标机器 IP 地址");
    return;
  }

  const resultBox = document.getElementById("probe-result-box");
  resultBox.className = "probe-result-box";
  resultBox.classList.remove("hidden");
  resultBox.innerHTML = `<div class="spinner"></div>正在向目标机器 ${escapeHtml(host)} 发送探测请求与拉取 NFS 共享列表...`;

  try {
    const res = await fetch("/api/probe/server", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host })
    });
    const data = await res.json();

    if (!data.ok || !data.ping.ok) {
      resultBox.className = "probe-result-box probe-error";
      resultBox.innerHTML = `❌ 目标机器网络无法连通：${data.ping ? data.ping.msg : "请检查网络或IP设置"}`;
      document.getElementById("btn-wizard-next").disabled = true;
      return;
    }

    if (!data.port.ok) {
      resultBox.className = "probe-result-box probe-error";
      resultBox.innerHTML = `⚠️ 目标机器网络正常，但 NFS 端口未开放。请检查目标机器的NFS访问权限。`;
      document.getElementById("btn-wizard-next").disabled = true;
      return;
    }

    if (data.export_error || !data.exports || data.exports.length === 0) {
      resultBox.className = "probe-result-box probe-error";
      resultBox.innerHTML = `⚠️ 未能拉取到导出的共享目录。<strong>${escapeHtml(data.export_error || "请检查目标机器的NFS访问权限")}</strong>`;
      document.getElementById("btn-wizard-next").disabled = true;
      return;
    }

    // 成功
    wizardData.server = host;
    wizardData.exportsList = data.exports;
    resultBox.className = "probe-result-box probe-success";
    resultBox.innerHTML = `
      ✅ <strong>探测成功！</strong> 延迟: ${data.ping.latency_ms} ms<br>
      发现目标机器已导出 <strong>${data.exports.length}</strong> 个可用共享目录。请点击“下一步”选择需要挂载的目录。
    `;
    document.getElementById("btn-wizard-next").disabled = false;

  } catch (err) {
    resultBox.className = "probe-result-box probe-error";
    resultBox.innerHTML = `❌ 探测出错: ${err.message}。请检查目标机器的NFS访问权限。`;
    document.getElementById("btn-wizard-next").disabled = true;
  }
}

// 步骤 4 执行挂载与四重体检
async function executeWizardMount() {
  const chkNet = document.getElementById("chk-net");
  const chkPort = document.getElementById("chk-port");
  const chkRead = document.getElementById("chk-read");
  const chkWrite = document.getElementById("chk-write");
  const finalMsg = document.getElementById("wizard-final-msg");

  chkNet.className = "check-item"; chkNet.innerHTML = "<span>⏳</span> 目标机器网络连通性...";
  chkPort.className = "check-item"; chkPort.innerHTML = "<span>⏳</span> NFS 2049 端口握手...";
  chkRead.className = "check-item"; chkRead.innerHTML = "<span>⏳</span> 写入系统策略与目录安全读取测试...";
  chkWrite.className = "check-item"; chkWrite.innerHTML = "<span>⏳</span> 临时探针写入与落盘验证...";

  try {
    const res = await fetch("/api/mounts/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        server: wizardData.server,
        export: wizardData.export,
        mountpoint: wizardData.mountpoint,
        preset: wizardData.preset
      })
    });
    const data = await res.json();

    if (!data.ok) {
      finalMsg.className = "final-msg-box probe-error";
      finalMsg.innerHTML = `❌ 挂载配置失败: ${escapeHtml(data.error)}`;
      finalMsg.classList.remove("hidden");
      return;
    }

    const h = data.health || {};
    // 网络
    chkNet.className = `check-item ${h.ping && h.ping.ok ? "ok" : "err"}`;
    chkNet.innerHTML = `${h.ping && h.ping.ok ? "✅" : "❌"} 目标机器网络连通性: ${h.ping ? h.ping.msg : "正常"}`;

    // 端口
    chkPort.className = `check-item ${h.port && h.port.ok ? "ok" : "err"}`;
    chkPort.innerHTML = `${h.port && h.port.ok ? "✅" : "❌"} NFS 端口握手: ${h.port ? h.port.msg : "正常"}`;

    // 读取测试
    chkRead.className = `check-item ${h.read && h.read.ok ? "ok" : "err"}`;
    chkRead.innerHTML = `${h.read && h.read.ok ? "✅" : "❌"} 目录安全读取测试: ${h.read && h.read.ok ? `耗时 ${h.read.latency_ms} ms` : (h.read ? h.read.msg : "失败")}`;

    // 写入测试
    chkWrite.className = `check-item ${h.write && h.write.ok ? "ok" : "err"}`;
    chkWrite.innerHTML = `${h.write && h.write.ok ? "✅" : "❌"} 临时探针写入验证: ${h.write && h.write.ok ? `耗时 ${h.write.latency_ms} ms (可读写)` : (h.write ? h.write.msg : "不可写")}`;

    finalMsg.className = "final-msg-box probe-success";
    finalMsg.innerHTML = `🎉 <strong>挂载完成！</strong> ${h.diagnostic_tip || "存储已顺利上线并受 FNHyperMount 守护。"}`;
    finalMsg.classList.remove("hidden");

  } catch (err) {
    finalMsg.className = "final-msg-box probe-error";
    finalMsg.innerHTML = `❌ 操作异常: ${err.message}`;
    finalMsg.classList.remove("hidden");
  }
}

// ==================== 操作按钮 ====================

async function triggerHealthCheck(mountpoint, server) {
  const modal = document.getElementById("health-modal");
  const body = document.getElementById("health-modal-body");
  document.getElementById("health-modal-title").textContent = `🩺 挂载点深度体检: ${mountpoint}`;
  body.innerHTML = `
    <div class="spinner"></div>
    <p style="text-align:center;">正在向目标机器执行 Ping、端口握手、读取及真实写入探测...</p>
  `;
  modal.classList.remove("hidden");

  try {
    const res = await fetch("/api/mounts/health", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mountpoint, server })
    });
    const data = await res.json();
    if (!data.ok) {
      body.innerHTML = `<div class="probe-result-box probe-error">体检失败: ${escapeHtml(data.error)}</div>`;
      return;
    }

    const r = data.report;
    body.innerHTML = `
      <div class="checklist">
        <div class="check-item ${r.ping.ok ? "ok" : "err"}">
          <span>${r.ping.ok ? "✅" : "❌"}</span> 目标机器网络响应：${r.ping.msg}
        </div>
        <div class="check-item ${r.port.ok ? "ok" : "err"}">
          <span>${r.port.ok ? "✅" : "❌"}</span> NFS 2049 端口：${r.port.msg}
        </div>
        <div class="check-item ${r.read.ok ? "ok" : "err"}">
          <span>${r.read.ok ? "✅" : "❌"}</span> 目录读取延时：${r.read.ok ? `${r.read.latency_ms} ms` : r.read.msg}
        </div>
        <div class="check-item ${r.write.ok ? "ok" : "err"}">
          <span>${r.write.ok ? "✅" : "❌"}</span> 写入落盘延时：${r.write.ok ? `${r.write.latency_ms} ms (可写)` : r.write.msg}
        </div>
      </div>
      <div class="final-msg-box ${r.summary_ok ? "probe-success" : "probe-error"}">
        <strong>体检结论：</strong> ${escapeHtml(r.diagnostic_tip || "连接状态良好")}
      </div>
    `;
  } catch (err) {
    body.innerHTML = `<div class="probe-result-box probe-error">网络通讯异常: ${err.message}</div>`;
  }
}

function closeHealthModal() {
  document.getElementById("health-modal").classList.add("hidden");
}

// ==================== 就地深度体检与链接信息刷新 ====================

function renderInlineHealth(mountpoint, server) {
  const item = healthCache[mountpoint];
  if (!item) {
    return `<span class="health-loading"><span>⏳</span><span>等待体检链接...</span></span>`;
  }
  if (item.loading) {
    return `<span class="health-loading"><span class="spin">🔄</span><span>正在探测网络延时、NFS端口及读写落盘...</span></span>`;
  }
  if (!item.ok || !item.report) {
    return `<span class="health-badge health-badge-err">❌ 探针异常: ${escapeHtml(item.error || "无法获取状态")}</span>`;
  }

  const r = item.report;
  const pingOk = r.ping && r.ping.ok;
  const portOk = r.port && r.port.ok;
  const readOk = r.read && r.read.ok;
  const writeOk = r.write && r.write.ok;

  const pingText = pingOk ? (r.ping.latency_ms !== undefined ? `${r.ping.latency_ms} ms` : r.ping.msg) : "超时";
  const portText = portOk ? "正常" : "阻断";
  const readText = readOk ? `${r.read.latency_ms} ms` : (r.read ? r.read.msg : "超时");
  const writeText = writeOk ? `${r.write.latency_ms} ms (RW)` : (r.write && r.write.writable === false ? "RO只读" : "失败");

  return `
    <span class="health-badge ${pingOk ? 'health-badge-ok' : 'health-badge-err'}" title="${escapeHtml(r.ping?.msg || '')}">
      🌐 网络: ${pingText}
    </span>
    <span class="health-badge ${portOk ? 'health-badge-ok' : 'health-badge-err'}" title="${escapeHtml(r.port?.msg || '')}">
      🔌 端口: ${portText}
    </span>
    <span class="health-badge ${readOk ? 'health-badge-ok' : 'health-badge-err'}" title="目录读取耗时: ${readOk ? r.read.latency_ms + 'ms' : r.read?.msg}">
      📖 读取: ${readText}
    </span>
    <span class="health-badge ${writeOk ? 'health-badge-ok' : (r.write?.writable === false ? 'health-badge-warn' : 'health-badge-err')}" title="探针测试写入落盘耗时: ${writeOk ? r.write.latency_ms + 'ms' : r.write?.msg}">
      ✏️ 写入: ${writeText}
    </span>
    ${item.timeStr ? `<span class="health-time-tag">${item.timeStr} 更新</span>` : ''}
  `;
}

async function refreshLinkHealth(mountpoint, server, cardId, silent = false) {
  if (healthCache[mountpoint] && healthCache[mountpoint].loading) {
    return;
  }

  healthCache[mountpoint] = { ...(healthCache[mountpoint] || {}), loading: true };
  const box = document.getElementById(`health-box-${cardId}`);
  const btn = document.getElementById(`btn-probe-${cardId}`);

  if (box) {
    box.innerHTML = renderInlineHealth(mountpoint, server);
  }
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="spin">🔄</span> 探测中...`;
  }

  try {
    const res = await fetch("/api/mounts/health", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mountpoint, server })
    });
    const data = await res.json();
    const now = new Date();
    const timeStr = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;

    if (data.ok) {
      healthCache[mountpoint] = {
        ok: true,
        report: data.report,
        loading: false,
        timeStr
      };
    } else {
      healthCache[mountpoint] = {
        ok: false,
        error: data.error || "体检失败",
        loading: false,
        timeStr
      };
    }
  } catch (err) {
    healthCache[mountpoint] = {
      ok: false,
      error: err.message,
      loading: false,
      timeStr: ""
    };
  } finally {
    const currentBox = document.getElementById(`health-box-${cardId}`);
    const currentBtn = document.getElementById(`btn-probe-${cardId}`);
    if (currentBox) {
      currentBox.innerHTML = renderInlineHealth(mountpoint, server);
    }
    if (currentBtn) {
      currentBtn.disabled = false;
      currentBtn.innerHTML = `🔄 刷新链接信息`;
    }
  }
}

async function triggerRemount(mountpoint) {
  if (!confirm(`确定要重新挂载 ${mountpoint} 吗？`)) return;
  try {
    const res = await fetch("/api/mounts/remount", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mountpoint })
    });
    const data = await res.json();
    alert(data.msg || "重新挂载信号已发送");
    loadMounts();
  } catch (err) {
    alert("操作失败: " + err.message);
  }
}

async function triggerRemove(mountpoint) {
  if (!confirm(`⚠️ 警告：确定要卸载并移除挂载点 ${mountpoint} 吗？\n（这仅会移除飞牛本地挂载配置，不会影响目标机器上的原文件）`)) return;
  try {
    const res = await fetch("/api/mounts/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mountpoint })
    });
    const data = await res.json();
    alert(data.msg || "挂载点已移除");
    loadMounts();
  } catch (err) {
    alert("操作失败: " + err.message);
  }
}

// 辅助函数
function formatSpeed(bps) {
  if (bps < 1024) return `${bps.toFixed(0)} B/s`;
  if (bps < 1024 * 1024) return `${(bps / 1024).toFixed(1)} KB/s`;
  if (bps < 1024 * 1024 * 1024) return `${(bps / (1024 * 1024)).toFixed(1)} MB/s`;
  return `${(bps / (1024 * 1024 * 1024)).toFixed(2)} GB/s`;
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
