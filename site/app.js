const STORE = {
  favorites: "shiguang_favorites_v1",
  history: "shiguang_history_v1",
  disliked: "shiguang_disliked_v1",
  backupVersion: 1
};

const state = {
  cards: [],
  visible: [],
  index: 0,
  view: "discover",
  category: "综合",
  detailFromList: false,
  installPrompt: null
};

const categories = ["综合", "生物", "科学", "历史", "艺术", "科技", "生活"];

const dom = {
  updateStatus: document.querySelector("#updateStatus"),
  dataButton: document.querySelector("#dataButton"),
  installButton: document.querySelector("#installButton"),
  tabs: [...document.querySelectorAll(".main-tab")],
  favoriteCount: document.querySelector("#favoriteCount"),
  categoryNav: document.querySelector("#categoryNav"),
  loadingState: document.querySelector("#loadingState"),
  emptyState: document.querySelector("#emptyState"),
  emptyTitle: document.querySelector("#emptyTitle"),
  emptyText: document.querySelector("#emptyText"),
  listView: document.querySelector("#listView"),
  listEyebrow: document.querySelector("#listEyebrow"),
  listTitle: document.querySelector("#listTitle"),
  listCount: document.querySelector("#listCount"),
  listItems: document.querySelector("#listItems"),
  knowledgeView: document.querySelector("#knowledgeView"),
  backToListButton: document.querySelector("#backToListButton"),
  cardNumber: document.querySelector("#cardNumber"),
  categoryLabel: document.querySelector("#categoryLabel"),
  dateLabel: document.querySelector("#dateLabel"),
  cardTitle: document.querySelector("#cardTitle"),
  cardLead: document.querySelector("#cardLead"),
  cardExplanation: document.querySelector("#cardExplanation"),
  cardAngle: document.querySelector("#cardAngle"),
  moreSection: document.querySelector("#moreSection"),
  closeMoreButton: document.querySelector("#closeMoreButton"),
  evidenceText: document.querySelector("#evidenceText"),
  sourceLink: document.querySelector("#sourceLink"),
  dock: document.querySelector("#dock"),
  previousButton: document.querySelector("#previousButton"),
  dislikeButton: document.querySelector("#dislikeButton"),
  interestButton: document.querySelector("#interestButton"),
  favoriteButton: document.querySelector("#favoriteButton"),
  nextButton: document.querySelector("#nextButton"),
  dataDialog: document.querySelector("#dataDialog"),
  closeDataButton: document.querySelector("#closeDataButton"),
  totalCards: document.querySelector("#totalCards"),
  totalFavorites: document.querySelector("#totalFavorites"),
  totalHistory: document.querySelector("#totalHistory"),
  exportButton: document.querySelector("#exportButton"),
  importInput: document.querySelector("#importInput"),
  restoreButton: document.querySelector("#restoreButton"),
  toast: document.querySelector("#toast")
};

function readStore(key, fallback = []) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

function writeStore(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function getFavorites() {
  return readStore(STORE.favorites);
}

function getHistory() {
  return readStore(STORE.history);
}

function getDisliked() {
  return readStore(STORE.disliked);
}

function shuffle(items) {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

function currentCard() {
  return state.visible[state.index];
}

function formatDate(value) {
  if (!value) return "已整理";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "已整理";
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function renderCategories() {
  dom.categoryNav.innerHTML = "";

  categories.forEach((category) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "category-chip";
    button.textContent = category;
    button.classList.toggle("active", category === state.category);

    button.addEventListener("click", () => {
      state.category = category;
      state.index = 0;
      state.detailFromList = false;
      renderCategories();
      closeMore();
      applyFilters(state.view === "discover");
      render();
    });

    dom.categoryNav.appendChild(button);
  });
}

function applyFilters(reshuffle = false) {
  let cards = [];

  if (state.view === "favorites") {
    const cardMap = new Map(state.cards.map((card) => [card.id, card]));
    cards = getFavorites().map((id) => cardMap.get(id)).filter(Boolean);
  } else if (state.view === "history") {
    const cardMap = new Map(state.cards.map((card) => [card.id, card]));
    cards = getHistory().map((entry) => cardMap.get(entry.id)).filter(Boolean);
  } else {
    const disliked = new Set(getDisliked());
    cards = state.cards.filter((card) => !disliked.has(card.id));
    if (reshuffle) cards = shuffle(cards);
  }

  if (state.category !== "综合") {
    cards = cards.filter((card) => card.category === state.category);
  }

  state.visible = cards;
  state.index = Math.min(state.index, Math.max(cards.length - 1, 0));
}

function setView(view) {
  state.view = view;
  state.category = "综合";
  state.index = 0;
  state.detailFromList = false;

  dom.tabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === view);
  });

  renderCategories();
  closeMore();
  applyFilters(view === "discover");
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function markHistory(card) {
  if (!card) return;

  let history = getHistory().filter((entry) => entry.id !== card.id);
  history.unshift({ id: card.id, viewedAt: new Date().toISOString() });
  writeStore(STORE.history, history.slice(0, 300));
}

function render() {
  dom.favoriteCount.textContent = String(getFavorites().length);
  dom.loadingState.hidden = true;

  const listMode = state.view !== "discover" && !state.detailFromList;

  dom.listView.hidden = !listMode;
  dom.knowledgeView.hidden = listMode || !currentCard();
  dom.dock.hidden = listMode;
  dom.emptyState.hidden = true;

  if (listMode) {
    renderCollectionList();
    return;
  }

  renderCard();
}

function renderCollectionList() {
  const isFavorites = state.view === "favorites";
  dom.listEyebrow.textContent = isFavorites ? "SAVED" : "RECENT";
  dom.listTitle.textContent = isFavorites ? "我的收藏" : "最近看过";
  dom.listCount.textContent = `${state.visible.length} 条`;
  dom.listItems.innerHTML = "";

  if (!state.visible.length) {
    showEmpty(
      isFavorites ? "还没有收藏" : "还没有阅读历史",
      isFavorites
        ? "在推荐页看到喜欢的内容时，点一下收藏按钮。"
        : "打开过的知识会自动出现在这里。"
    );
    return;
  }

  state.visible.forEach((card) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "collection-item";
    item.dataset.cardId = card.id;

    const meta = document.createElement("div");
    meta.className = "collection-item-meta";

    const category = document.createElement("span");
    category.textContent = card.category || "综合";

    const date = document.createElement("time");
    date.textContent = formatDate(card.created_at);

    meta.append(category, date);

    const title = document.createElement("h2");
    title.textContent = card.title;

    const lead = document.createElement("p");
    lead.textContent = card.lead;

    const arrow = document.createElement("span");
    arrow.className = "collection-arrow";
    arrow.textContent = "›";
    arrow.setAttribute("aria-hidden", "true");

    item.append(meta, title, lead, arrow);
    item.addEventListener("click", () => openListCard(card.id));
    dom.listItems.appendChild(item);
  });
}

function openListCard(cardId) {
  const index = state.visible.findIndex((card) => card.id === cardId);
  if (index < 0) return;

  state.index = index;
  state.detailFromList = true;
  closeMore();
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function returnToList() {
  state.detailFromList = false;
  closeMore();
  applyFilters();
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderCard() {
  const card = currentCard();

  dom.listView.hidden = true;
  dom.dock.hidden = false;

  if (!card) {
    dom.knowledgeView.hidden = true;
    showEmpty(
      state.view === "discover" ? "暂时没有推荐内容" : "这条内容已经不在列表中",
      state.view === "discover"
        ? "可以在“数据”中恢复不感兴趣的内容。"
        : "返回列表继续查看其他内容。"
    );
    updateControls();
    return;
  }

  dom.emptyState.hidden = true;
  dom.knowledgeView.hidden = false;
  dom.backToListButton.hidden = !state.detailFromList;
  dom.cardNumber.textContent = String(state.index + 1).padStart(3, "0");
  dom.categoryLabel.textContent = card.category || "综合";
  dom.dateLabel.textContent = formatDate(card.created_at);
  dom.cardTitle.textContent = card.title;
  dom.cardLead.textContent = card.lead;
  dom.cardExplanation.textContent = card.explanation;
  dom.cardAngle.textContent = card.angle;
  dom.evidenceText.textContent = `来源依据：${card.evidence || "请查看原始来源。"}`;
  dom.sourceLink.href = card.source_url;
  dom.sourceLink.textContent = `查看原始来源：${card.source_name || "原文"} ↗`;

  const saved = getFavorites().includes(card.id);
  dom.favoriteButton.textContent = saved ? "♥" : "♡";
  dom.favoriteButton.classList.toggle("saved", saved);

  dom.dislikeButton.hidden = state.view !== "discover";
  markHistory(card);
  updateControls();
}

function showEmpty(title, text) {
  dom.listView.hidden = true;
  dom.knowledgeView.hidden = true;
  dom.dock.hidden = true;
  dom.emptyState.hidden = false;
  dom.emptyTitle.textContent = title;
  dom.emptyText.textContent = text;
}

function updateControls() {
  const hasCard = Boolean(currentCard());
  const multiple = state.visible.length > 1;

  dom.previousButton.disabled = !hasCard || !multiple;
  dom.dislikeButton.disabled = !hasCard;
  dom.interestButton.disabled = !hasCard;
  dom.favoriteButton.disabled = !hasCard;
  dom.nextButton.disabled = !hasCard || !multiple;
}

function move(step) {
  if (!state.visible.length) return;

  closeMore();
  state.index = (state.index + step + state.visible.length) % state.visible.length;
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function dislikeCurrent() {
  if (state.view !== "discover") return;

  const card = currentCard();
  if (!card) return;

  const disliked = getDisliked();
  if (!disliked.includes(card.id)) disliked.unshift(card.id);
  writeStore(STORE.disliked, disliked.slice(0, 300));

  state.visible = state.visible.filter((item) => item.id !== card.id);
  state.index = Math.min(state.index, Math.max(state.visible.length - 1, 0));
  closeMore();
  render();
  showToast("已减少这类内容");
}

function toggleFavorite() {
  const card = currentCard();
  if (!card) return;

  let favorites = getFavorites();
  const removing = favorites.includes(card.id);

  if (removing) {
    favorites = favorites.filter((id) => id !== card.id);
    showToast("已取消收藏");
  } else {
    favorites.unshift(card.id);
    showToast("已收藏");
  }

  writeStore(STORE.favorites, favorites);

  if (state.view === "favorites" && removing) {
    state.detailFromList = false;
    applyFilters();
  }

  render();
}

function showMore() {
  if (!currentCard()) return;

  dom.moreSection.hidden = false;
  dom.interestButton.textContent = "已展开";
  dom.moreSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeMore() {
  dom.moreSection.hidden = true;
  dom.interestButton.textContent = "感兴趣";
}

function showToast(message) {
  dom.toast.textContent = message;
  dom.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => dom.toast.classList.remove("show"), 1600);
}

function openDataDialog() {
  dom.totalCards.textContent = String(state.cards.length);
  dom.totalFavorites.textContent = String(getFavorites().length);
  dom.totalHistory.textContent = String(getHistory().length);
  dom.dataDialog.showModal();
}

function exportData() {
  const payload = {
    version: STORE.backupVersion,
    exportedAt: new Date().toISOString(),
    favorites: getFavorites(),
    history: getHistory(),
    disliked: getDisliked()
  };

  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json"
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `拾光记录_${new Date().toISOString().slice(0, 10)}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
  showToast("记录已导出");
}

async function importData(file) {
  if (!file) return;

  try {
    const payload = JSON.parse(await file.text());
    if (Array.isArray(payload.favorites)) writeStore(STORE.favorites, payload.favorites);
    if (Array.isArray(payload.history)) writeStore(STORE.history, payload.history);
    if (Array.isArray(payload.disliked)) writeStore(STORE.disliked, payload.disliked);

    state.detailFromList = false;
    applyFilters(state.view === "discover");
    render();
    dom.dataDialog.close();
    showToast("记录已导入");
  } catch {
    showToast("无法读取这个备份文件");
  } finally {
    dom.importInput.value = "";
  }
}

function restoreDisliked() {
  writeStore(STORE.disliked, []);
  state.detailFromList = false;
  applyFilters(true);
  render();
  dom.dataDialog.close();
  showToast("已恢复隐藏内容");
}

async function loadCards() {
  try {
    const response = await fetch(`data/cards.json?t=${Date.now()}`, {
      cache: "no-store"
    });

    if (!response.ok) throw new Error("读取失败");

    const payload = await response.json();
    state.cards = Array.isArray(payload.cards) ? payload.cards : [];
    dom.updateStatus.textContent = `${state.cards.length}条 · ${formatDate(payload.updated_at)}`;
    applyFilters(true);
    render();
  } catch {
    dom.loadingState.textContent = "暂时无法读取知识数据，请稍后刷新。";
    dom.updateStatus.textContent = "读取失败";
  }
}

function bindEvents() {
  dom.tabs.forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });

  dom.backToListButton.addEventListener("click", returnToList);
  dom.previousButton.addEventListener("click", () => move(-1));
  dom.nextButton.addEventListener("click", () => move(1));
  dom.dislikeButton.addEventListener("click", dislikeCurrent);
  dom.interestButton.addEventListener("click", showMore);
  dom.closeMoreButton.addEventListener("click", closeMore);
  dom.favoriteButton.addEventListener("click", toggleFavorite);

  dom.dataButton.addEventListener("click", openDataDialog);
  dom.closeDataButton.addEventListener("click", () => dom.dataDialog.close());
  dom.exportButton.addEventListener("click", exportData);
  dom.importInput.addEventListener("change", () => importData(dom.importInput.files[0]));
  dom.restoreButton.addEventListener("click", restoreDisliked);

  dom.dataDialog.addEventListener("click", (event) => {
    if (event.target === dom.dataDialog) dom.dataDialog.close();
  });

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    state.installPrompt = event;
    dom.installButton.hidden = false;
  });

  dom.installButton.addEventListener("click", async () => {
    if (!state.installPrompt) return;

    state.installPrompt.prompt();
    await state.installPrompt.userChoice;
    state.installPrompt = null;
    dom.installButton.hidden = true;
  });
}

function registerServiceWorker() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("./service-worker.js").catch(() => {});
  }
}

function init() {
  renderCategories();
  bindEvents();
  loadCards();
  registerServiceWorker();
}

init();
