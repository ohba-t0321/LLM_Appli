const $ = (id) => document.getElementById(id);

function getSettings() {
  return {
    provider: localStorage.getItem("provider") || "openai",
    model: localStorage.getItem("model") || "gpt-4o-mini",
    api_key: localStorage.getItem("api_key") || "",
  };
}

function setSettings({ provider, model, api_key }) {
  localStorage.setItem("provider", provider);
  localStorage.setItem("model", model);
  localStorage.setItem("api_key", api_key);
}

function addMessage(role, text) {
  const box = document.createElement("div");
  box.className = `msg ${role}`;
  box.textContent = text;
  $("messages").appendChild(box);
  $("messages").scrollTop = $("messages").scrollHeight;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "API error");
  return data;
}

async function loadKnowledge() {
  const data = await api("/api/knowledge");
  const list = $("kbList");
  list.innerHTML = "";
  data.items.forEach((item) => {
    const li = document.createElement("li");
    li.className = "kb-item";
    li.innerHTML = `<b>${item.title}</b><br>${item.content.slice(0, 120)}...`;
    const del = document.createElement("button");
    del.textContent = "削除";
    del.onclick = async () => {
      await fetch(`/api/knowledge?id=${item.id}`, { method: "DELETE" });
      loadKnowledge();
    };
    li.appendChild(del);
    list.appendChild(li);
  });
}

async function readFileText(fileInputId) {
  const file = $(fileInputId).files[0];
  if (!file) return "";
  return await file.text();
}

window.addEventListener("DOMContentLoaded", async () => {
  const s = getSettings();
  $("provider").value = s.provider;
  $("model").value = s.model;
  $("apiKey").value = s.api_key;

  $("openSettings").onclick = () => $("settingsDialog").showModal();
  $("closeSettings").onclick = () => $("settingsDialog").close();
  $("saveSettings").onclick = () => {
    setSettings({
      provider: $("provider").value,
      model: $("model").value,
      api_key: $("apiKey").value,
    });
    $("settingsDialog").close();
    addMessage("assistant", "設定を保存しました。APIトークンはこのブラウザに保存されます。");
  };

  $("addKb").onclick = async () => {
    try {
      await api("/api/knowledge", {
        method: "POST",
        body: JSON.stringify({ title: $("kbTitle").value, content: $("kbContent").value }),
      });
      $("kbTitle").value = "";
      $("kbContent").value = "";
      await loadKnowledge();
      addMessage("assistant", "ナレッジを保存しました。");
    } catch (e) {
      addMessage("assistant", `エラー: ${e.message}`);
    }
  };

  $("sendMessage").onclick = async () => {
    try {
      const message = $("messageInput").value.trim();
      if (!message) return;
      addMessage("user", message);
      $("messageInput").value = "";

      const settings = getSettings();
      const data = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({ ...settings, message }),
      });
      addMessage("assistant", data.response);
    } catch (e) {
      addMessage("assistant", `エラー: ${e.message}`);
    }
  };

  $("generateContract").onclick = async () => {
    try {
      const request_text = $("messageInput").value.trim();
      if (!request_text) {
        addMessage("assistant", "契約書作成の要件を入力してください。");
        return;
      }
      addMessage("user", `[契約書作成依頼]\n${request_text}`);
      $("messageInput").value = "";
      const settings = getSettings();
      const data = await api("/api/generate_contract", {
        method: "POST",
        body: JSON.stringify({ ...settings, request_text }),
      });
      addMessage("assistant", data.draft);
    } catch (e) {
      addMessage("assistant", `エラー: ${e.message}`);
    }
  };

  $("runBatch").onclick = async () => {
    try {
      const document_text = await readFileText("inputFile");
      const prompt_csv_text = await readFileText("promptCsv");
      if (!document_text || !prompt_csv_text) {
        addMessage("assistant", "入力ファイルとプロンプトCSVの両方を選択してください。");
        return;
      }
      const settings = getSettings();
      const data = await api("/api/batch", {
        method: "POST",
        body: JSON.stringify({ ...settings, document_text, prompt_csv_text }),
      });
      addMessage("assistant", `一括分析が完了しました（job_id=${data.job_id}）`);
      data.results.forEach((r) => {
        addMessage("assistant", `[${r.prompt_name}]\n${r.status === "completed" ? r.response : "失敗: " + r.error_message}`);
      });
    } catch (e) {
      addMessage("assistant", `エラー: ${e.message}`);
    }
  };

  await loadKnowledge();
  addMessage("assistant", "こんにちは。設定からAPIトークンを入れると、OpenAI APIで回答できます。");
});
