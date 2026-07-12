const STORE = {
  favorites: "shiguang_favorites_v1",
  disliked: "shiguang_disliked_v1",
  read: "shiguang_read_v2",
  later: "shiguang_later_v2",
  daily: "shiguang_daily_v2",
  randomQueue: "shiguang_random_queue_v1",
  triggerEndpoint: "shiguang_trigger_endpoint_v1",
  triggerSecret: "shiguang_trigger_secret_v1",
  activeRun: "shiguang_active_run_v1",
  lastSupplyState: "shiguang_last_supply_state_v1",
  backupVersion: 2
};

const DAILY_SIZE = 5;
const PAGE_SIZE = 20;
const CATEGORIES = ["全部", "生物", "科学", "历史", "艺术", "科技", "生活", "综合"];

const state = {
  cards: [],
  cardMap: new Map(),
  view: "daily",
  detailOrigin: "daily",
  currentCardId: null,
  dailyRecord: null,
  exploreMode: "random",
  randomCardId: null,
  exploreStatus: "unread",
  exploreCategory: "全部",
  exploreSort: "newest",
  exploreLimit: PAGE_SIZE,
  mineSection: "favorites",
  loadingSupply: false,
  installPrompt: null
};

const PROGRESS_STATUSES = new Set(["read", "favorite", "explored"]);
const runtimeConfig = window.SHIGUANG_CONFIG || {};
const supabaseConfig = {
  url: String(runtimeConfig.SUPABASE_URL || "").replace(/\/+$/, ""),
  key: String(runtimeConfig.SUPABASE_KEY || "")
};

const dom = {
  brandButton: document.querySelector("#brandButton"),
  updateStatus: document.querySelector("#updateStatus"),
  dataButton: document.querySelector("#dataButton"),
  installButton: document.querySelector("#installButton"),
  mainTabs: [...document.querySelectorAll(".main-tab")],
  dailyBadge: document.querySelector("#dailyBadge"),

  loadingState: document.querySelector("#loadingState"),
  emptyState: document.querySelector("#emptyState"),
  emptyTitle: document.querySelector("#emptyTitle"),
  emptyText: document.querySelector("#emptyText"),
  emptyActionButton: document.querySelector("#emptyActionButton"),

  dailyHome: document.querySelector("#dailyHome"),
  todayLabel: document.querySelector("#todayLabel"),
  dailySummary: document.querySelector("#dailySummary"),
  dailyRemaining: document.querySelector("#dailyRemaining"),
  dailyList: document.querySelector("#dailyList"),

  exploreHome: document.querySelector("#exploreHome"),
  exploreCount: document.querySelector("#exploreCount"),
  exploreModeTabs: [...document.querySelectorAll(".explore-mode-tab")],
  randomExplorePanel: document.querySelector("#randomExplorePanel"),
  libraryExplorePanel: document.querySelector("#libraryExplorePanel"),
  randomUnreadCount: document.querySelector("#randomUnreadCount"),
  randomCard: document.querySelector("#randomCard"),
  randomEmpty: document.querySelector("#randomEmpty"),
  randomCardNumber: document.querySelector("#randomCardNumber"),
  randomCategoryLabel: document.querySelector("#randomCategoryLabel"),
  randomCardTitle: document.querySelector("#randomCardTitle"),
  randomCardLead: document.querySelector("#randomCardLead"),
  randomCardExplanation: document.querySelector("#randomCardExplanation"),
  randomCardAngle: document.querySelector("#randomCardAngle"),
  randomSkipButton: document.querySelector("#randomSkipButton"),
  randomLaterButton: document.querySelector("#randomLaterButton"),
  randomFavoriteButton: document.querySelector("#randomFavoriteButton"),
  randomDislikeButton: document.querySelector("#randomDislikeButton"),
  randomCompleteButton: document.querySelector("#randomCompleteButton"),
  randomSourceLink: document.querySelector("#randomSourceLink"),
  searchInput: document.querySelector("#searchInput"),
  statusFilters: [...document.querySelectorAll("#statusFilters .filter-chip")],
  categoryFilters: document.querySelector("#categoryFilters"),
  sortFilters: [...document.querySelectorAll("#sortFilters .filter-chip")],
  exploreList: document.querySelector("#exploreList"),
  showMoreButton: document.querySelector("#showMoreButton"),

  mineHome: document.querySelector("#mineHome"),
  mineFavoriteCount: document.querySelector("#mineFavoriteCount"),
  mineReadCount: document.querySelector("#mineReadCount"),
  mineLaterCount: document.querySelector("#mineLaterCount"),
  mineDislikedCount: document.querySelector("#mineDislikedCount"),
  summaryCards: [...document.querySelectorAll(".summary-card")],
  mineTabs: [...document.querySelectorAll(".mine-tab")],
  mineListTitle: document.querySelector("#mineListTitle"),
  mineListCount: document.querySelector("#mineListCount"),
  mineList: document.querySelector("#mineList"),

  detailView: document.querySelector("#detailView"),
  backButton: document.querySelector("#backButton"),
  cardNumber: document.querySelector("#cardNumber"),
  categoryLabel: document.querySelector("#categoryLabel"),
  readLabel: document.querySelector("#readLabel"),
  dateLabel: document.querySelector("#dateLabel"),
  cardTitle: document.querySelector("#cardTitle"),
  cardLead: document.querySelector("#cardLead"),
  cardExplanation: document.querySelector("#cardExplanation"),
  cardAngle: document.querySelector("#cardAngle"),
  sourceSection: document.querySelector("#sourceSection"),
  closeSourceButton: document.querySelector("#closeSourceButton"),
  evidenceText: document.querySelector("#evidenceText"),
  sourceLink: document.querySelector("#sourceLink"),

  detailDock: document.querySelector("#detailDock"),
  sourceButton: document.querySelector("#sourceButton"),
  laterButton: document.querySelector("#laterButton"),
  completeButton: document.querySelector("#completeButton"),
  favoriteButton: document.querySelector("#favoriteButton"),
  dislikeButton: document.querySelector("#dislikeButton"),

  dataDialog: document.querySelector("#dataDialog"),
  closeDataButton: document.querySelector("#closeDataButton"),
  totalCards: document.querySelector("#totalCards"),
  totalUnread: document.querySelector("#totalUnread"),
  totalFavorites: document.querySelector("#totalFavorites"),
  exportButton: document.querySelector("#exportButton"),
  importInput: document.querySelector("#importInput"),
  resetDailyButton: document.querySelector("#resetDailyButton"),
  triggerSettingsState: document.querySelector("#triggerSettingsState"),
  triggerEndpointInput: document.querySelector("#triggerEndpointInput"),
  triggerSecretInput: document.querySelector("#triggerSecretInput"),
  saveTriggerSettingsButton: document.querySelector("#saveTriggerSettingsButton"),
  replenishButton: document.querySelector("#replenishButton"),
  poolStatusTime: document.querySelector("#poolStatusTime"),
  pendingCandidateCount: document.querySelector("#pendingCandidateCount"),
  harvestFetchedCount: document.querySelector("#harvestFetchedCount"),
  harvestAddedCount: document.querySelector("#harvestAddedCount"),
  harvestPoolAfter: document.querySelector("#harvestPoolAfter"),
  harvestStatusNote: document.querySelector("#harvestStatusNote"),
  lastAddedCount: document.querySelector("#lastAddedCount"),
  lastPassRate: document.querySelector("#lastPassRate"),
  lastTokenCount: document.querySelector("#lastTokenCount"),
  lastProcessedCount: document.querySelector("#lastProcessedCount"),
  poolStatusNote: document.querySelector("#poolStatusNote"),
  supplyTaskCard: document.querySelector("#supplyTaskCard"),
  supplyTaskTitle: document.querySelector("#supplyTaskTitle"),
  supplyTaskDetail: document.querySelector("#supplyTaskDetail"),
  supplyTaskLink: document.querySelector("#supplyTaskLink"),

  toast: document.querySelector("#toast")
};

function readJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function getSet(key) {
  return new Set(readJson(key, []));
}

function saveSet(key, values) {
  writeJson(key, [...values]);
}

function canRecordProgress() {
  return Boolean(supabaseConfig.url && supabaseConfig.key);
}

function recordProgress(cardId, status) {
  if (!cardId || !PROGRESS_STATUSES.has(status) || !canRecordProgress()) {
    return Promise.resolve(false);
  }

  return fetch(`${supabaseConfig.url}/rest/v1/user_progress`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: supabaseConfig.key,
      Authorization: `Bearer ${supabaseConfig.key}`,
      Prefer: "return=minimal"
    },
    body: JSON.stringify({
      card_id: cardId,
      status
    })
  })
    .then((response) => response.ok)
    .catch(() => false);
}

function getFavorites() {
  return getSet(STORE.favorites);
}

function getRead() {
  return getSet(STORE.read);
}

function getLater() {
  return getSet(STORE.later);
}

function getDisliked() {
  return getSet(STORE.disliked);
}

function localDateKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatToday() {
  const date = new Date();
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function formatDate(value) {
  if (!value) return "已整理";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "已整理";
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function stableHash(text) {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function normalizeSearch(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/\s+/g, "");
}

function isUnread(cardId) {
  return !getRead().has(cardId);
}

function ensureDailyRecord(force = false) {
  const date = localDateKey();
  const read = getRead();
  const later = getLater();
  const disliked = getDisliked();
  const validIds = new Set(state.cards.map((card) => card.id));

  let record = readJson(STORE.daily, null);

  if (
    force ||
    !record ||
    record.date !== date ||
    !Array.isArray(record.ids)
  ) {
    const candidates = state.cards
      .filter((card) =>
        !read.has(card.id) &&
        !later.has(card.id) &&
        !disliked.has(card.id)
      )
      .sort((left, right) =>
        stableHash(`${date}|${left.id}`) - stableHash(`${date}|${right.id}`)
      );

    record = {
      date,
      ids: candidates.slice(0, DAILY_SIZE).map((card) => card.id),
      createdAt: new Date().toISOString()
    };
    writeJson(STORE.daily, record);
  } else {
    record.ids = record.ids.filter((id) => validIds.has(id));
    writeJson(STORE.daily, record);
  }

  state.dailyRecord = record;
  return record;
}

function dailyCards({ includeCompleted = false } = {}) {
  const record = ensureDailyRecord();
  const read = getRead();
  const later = getLater();
  const disliked = getDisliked();

  return record.ids
    .map((id) => state.cardMap.get(id))
    .filter(Boolean)
    .filter((card) => {
      if (includeCompleted) return true;
      return !read.has(card.id) && !later.has(card.id) && !disliked.has(card.id);
    });
}

function updateCounts() {
  const read = getRead();
  const favorites = getFavorites();
  const later = getLater();
  const disliked = getDisliked();
  const dailyUnread = dailyCards().length;

  dom.dailyBadge.textContent = String(dailyUnread);
  dom.mineFavoriteCount.textContent = String(favorites.size);
  dom.mineReadCount.textContent = String(read.size);
  dom.mineLaterCount.textContent = String(later.size);
  dom.mineDislikedCount.textContent = String(disliked.size);
}

function hideAllViews() {
  dom.loadingState.hidden = true;
  dom.emptyState.hidden = true;
  dom.dailyHome.hidden = true;
  dom.exploreHome.hidden = true;
  dom.mineHome.hidden = true;
  dom.detailView.hidden = true;
  dom.detailDock.hidden = true;
}

function setActiveMainTab(view) {
  dom.mainTabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === view);
  });
}

function setView(view) {
  state.view = view;
  state.currentCardId = null;
  dom.sourceSection.hidden = true;
  setActiveMainTab(view);
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showEmpty(title, text, actionText = "", action = null) {
  hideAllViews();
  dom.emptyState.hidden = false;
  dom.emptyTitle.textContent = title;
  dom.emptyText.textContent = text;

  if (actionText && action) {
    dom.emptyActionButton.hidden = false;
    dom.emptyActionButton.textContent = actionText;
    dom.emptyActionButton.onclick = action;
  } else {
    dom.emptyActionButton.hidden = true;
    dom.emptyActionButton.onclick = null;
  }
}

function render() {
  updateCounts();

  if (state.currentCardId) {
    renderDetail();
    return;
  }

  if (state.view === "daily") {
    renderDaily();
  } else if (state.view === "explore") {
    renderExplore();
  } else {
    renderMine();
  }
}

function renderDaily() {
  hideAllViews();
  dom.dailyHome.hidden = false;

  const record = ensureDailyRecord();
  const unreadCards = dailyCards();
  const allCards = dailyCards({ includeCompleted: true });
  const completedCount = Math.max(0, allCards.length - unreadCards.length);

  dom.todayLabel.textContent = formatToday();
  dom.dailyRemaining.textContent = String(unreadCards.length);

  if (!allCards.length) {
    showEmpty(
      "知识库里暂时没有可推荐内容",
      "探索页仍然可以查看已有知识；后台知识库补充后，明天会自动抽取新的每日推荐。",
      "去探索",
      () => setView("explore")
    );
    return;
  }

  if (!unreadCards.length) {
    showEmpty(
      "今天的几条已经看完",
      `今天共安排了 ${allCards.length} 条。现在可以去探索知识库，主动找些更感兴趣的内容。`,
      "去探索",
      () => setView("explore")
    );
    return;
  }

  dom.dailySummary.textContent = completedCount
    ? `今天已经看完 ${completedCount} 条，还剩 ${unreadCards.length} 条。`
    : `今天准备了 ${allCards.length} 条，读完就好，不必一次吞下整个知识库。`;

  dom.dailyList.innerHTML = "";

  unreadCards.forEach((card, index) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "daily-item";

    const number = document.createElement("span");
    number.className = "daily-index";
    number.textContent = String(index + 1).padStart(2, "0");

    const copy = document.createElement("div");
    const title = document.createElement("h2");
    title.textContent = card.title;
    const lead = document.createElement("p");
    lead.textContent = card.lead;
    copy.append(title, lead);

    const arrow = document.createElement("span");
    arrow.className = "daily-arrow";
    arrow.textContent = "›";

    item.append(number, copy, arrow);
    item.addEventListener("click", () => openDetail(card.id, "daily"));
    dom.dailyList.appendChild(item);
  });
}

function renderCategoryFilters() {
  dom.categoryFilters.innerHTML = "";

  CATEGORIES.forEach((category) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "filter-chip";
    button.textContent = category;
    button.classList.toggle("active", category === state.exploreCategory);
    button.addEventListener("click", () => {
      state.exploreCategory = category;
      state.exploreLimit = PAGE_SIZE;
      renderCategoryFilters();
      renderExplore();
    });
    dom.categoryFilters.appendChild(button);
  });
}

function filteredExploreCards() {
  const read = getRead();
  const favorites = getFavorites();
  const disliked = getDisliked();
  const query = normalizeSearch(dom.searchInput.value);

  let cards = state.cards.filter((card) => {
    if (state.exploreStatus === "unread" && read.has(card.id)) return false;
    if (state.exploreStatus === "read" && !read.has(card.id)) return false;
    if (state.exploreStatus === "favorite" && !favorites.has(card.id)) return false;

    if (
      state.exploreCategory !== "全部" &&
      card.category !== state.exploreCategory
    ) {
      return false;
    }

    if (query) {
      const haystack = normalizeSearch(
        `${card.title}${card.lead}${card.explanation}${card.category}`
      );
      if (!haystack.includes(query)) return false;
    }

    return true;
  });

  if (state.exploreSort === "newest") {
    cards.sort((a, b) =>
      String(b.created_at || "").localeCompare(String(a.created_at || ""))
    );
  } else if (state.exploreSort === "category") {
    cards.sort((a, b) =>
      String(a.category || "").localeCompare(String(b.category || ""), "zh-CN") ||
      String(a.title || "").localeCompare(String(b.title || ""), "zh-CN")
    );
  } else {
    const key = `${localDateKey()}|explore`;
    cards.sort((a, b) =>
      stableHash(`${key}|${a.id}`) - stableHash(`${key}|${b.id}`)
    );
  }

  return cards;
}

function renderKnowledgeList(container, cards, origin, emptyText) {
  container.innerHTML = "";

  if (!cards.length) {
    const empty = document.createElement("div");
    empty.className = "state-box";
    empty.style.margin = "24px 0";
    empty.innerHTML = `<div class="empty-symbol">◌</div><p>${emptyText}</p>`;
    container.appendChild(empty);
    return;
  }

  const read = getRead();
  const favorites = getFavorites();

  cards.forEach((card) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "knowledge-item";

    const meta = document.createElement("div");
    meta.className = "item-meta";

    const category = document.createElement("span");
    category.className = "item-tag";
    category.textContent = card.category || "综合";
    meta.appendChild(category);

    const status = document.createElement("span");
    status.className = `item-tag${read.has(card.id) ? " read" : ""}`;
    status.textContent = read.has(card.id) ? "已读" : "未读";
    meta.appendChild(status);

    if (favorites.has(card.id)) {
      const favorite = document.createElement("span");
      favorite.className = "item-tag favorite";
      favorite.textContent = "已收藏";
      meta.appendChild(favorite);
    }

    if (getDisliked().has(card.id)) {
      const hiddenTag = document.createElement("span");
      hiddenTag.className = "item-tag hidden-tag";
      hiddenTag.textContent = "不感兴趣";
      meta.appendChild(hiddenTag);
    }

    const date = document.createElement("time");
    date.textContent = formatDate(card.created_at);
    meta.appendChild(date);

    const title = document.createElement("h2");
    title.textContent = card.title;

    const lead = document.createElement("p");
    lead.textContent = card.lead;

    const arrow = document.createElement("span");
    arrow.className = "item-arrow";
    arrow.textContent = "›";

    item.append(meta, title, lead, arrow);
    item.addEventListener("click", () => openDetail(card.id, origin));
    container.appendChild(item);
  });
}


function shuffleIds(ids) {
  const copy = [...ids];
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [copy[index], copy[swapIndex]] = [copy[swapIndex], copy[index]];
  }
  return copy;
}

function randomEligibleCards() {
  const read = getRead();
  const later = getLater();
  const disliked = getDisliked();

  return state.cards.filter((card) =>
    !read.has(card.id) &&
    !later.has(card.id) &&
    !disliked.has(card.id)
  );
}

function loadRandomQueue() {
  const eligibleIds = new Set(randomEligibleCards().map((card) => card.id));
  let queue = readJson(STORE.randomQueue, [])
    .filter((id) => eligibleIds.has(id));

  if (!queue.length) {
    queue = shuffleIds([...eligibleIds]);
  }

  writeJson(STORE.randomQueue, queue);
  return queue;
}

function ensureRandomCard(forceNext = false) {
  const eligibleIds = new Set(randomEligibleCards().map((card) => card.id));

  if (
    !forceNext &&
    state.randomCardId &&
    eligibleIds.has(state.randomCardId)
  ) {
    return state.cardMap.get(state.randomCardId);
  }

  let queue = loadRandomQueue().filter((id) => id !== state.randomCardId);

  if (!queue.length) {
    state.randomCardId = null;
    return null;
  }

  state.randomCardId = queue.shift();
  writeJson(STORE.randomQueue, queue);
  recordProgress(state.randomCardId, "explored");
  return state.cardMap.get(state.randomCardId) || null;
}

function setExploreMode(mode) {
  state.exploreMode = mode;

  dom.exploreModeTabs.forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });

  if (mode === "random") {
    ensureRandomCard();
  }

  renderExplore();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderRandomExplore() {
  const eligible = randomEligibleCards();
  dom.randomUnreadCount.textContent = `${eligible.length} 条可探索`;
  dom.exploreCount.textContent = `${eligible.length} 条未读`;

  const card = ensureRandomCard();

  if (!card) {
    dom.randomCard.hidden = true;
    dom.randomEmpty.hidden = false;
    return;
  }

  dom.randomCard.hidden = false;
  dom.randomEmpty.hidden = true;

  const favorites = getFavorites();
  const later = getLater();

  dom.randomCardNumber.textContent = "RANDOM";
  dom.randomCategoryLabel.textContent = card.category || "综合";
  dom.randomCardTitle.textContent = card.title;
  dom.randomCardLead.textContent = card.lead;
  dom.randomCardExplanation.textContent = card.explanation;
  dom.randomCardAngle.textContent = card.angle;

  dom.randomFavoriteButton.textContent = favorites.has(card.id)
    ? "已收藏"
    : "收藏";
  dom.randomLaterButton.textContent = later.has(card.id)
    ? "已稍后"
    : "稍后再看";

  dom.randomSourceLink.href = card.source_url || "#";
  dom.randomSourceLink.textContent =
    `查看原始来源：${card.source_name || "原文"} ↗`;
}

function advanceRandomCard({ markRead = false, putBack = false } = {}) {
  const cardId = state.randomCardId;
  if (!cardId) return;

  if (markRead) {
    const read = getRead();
    read.add(cardId);
    saveSet(STORE.read, read);
    recordProgress(cardId, "read");
  }

  if (putBack) {
    const eligibleIds = new Set(randomEligibleCards().map((card) => card.id));
    const queue = loadRandomQueue().filter((id) => id !== cardId);
    if (eligibleIds.has(cardId)) queue.push(cardId);
    writeJson(STORE.randomQueue, queue);
  }

  state.randomCardId = null;
  ensureRandomCard(true);
  renderExplore();
}

function toggleRandomFavorite() {
  const cardId = state.randomCardId;
  if (!cardId) return;

  const favorites = getFavorites();

  if (favorites.has(cardId)) {
    favorites.delete(cardId);
    showToast("已取消收藏");
  } else {
    favorites.add(cardId);
    recordProgress(cardId, "favorite");
    showToast("已收藏");
  }

  saveSet(STORE.favorites, favorites);
  renderRandomExplore();
  updateCounts();
}

function toggleRandomLater() {
  const cardId = state.randomCardId;
  if (!cardId) return;

  const later = getLater();

  if (later.has(cardId)) {
    later.delete(cardId);
    saveSet(STORE.later, later);
    showToast("已移出稍后再看");
    renderRandomExplore();
    return;
  }

  later.add(cardId);
  saveSet(STORE.later, later);
  showToast("已加入稍后再看");

  state.randomCardId = null;
  ensureRandomCard(true);
  renderExplore();
}

function dislikeRandomCard() {
  const cardId = state.randomCardId;
  if (!cardId) return;

  const confirmed = window.confirm(
    "确定不再在每日推荐和随机探索中显示这条知识吗？"
  );
  if (!confirmed) return;

  const disliked = getDisliked();
  disliked.add(cardId);
  saveSet(STORE.disliked, disliked);

  state.randomCardId = null;
  ensureRandomCard(true);
  showToast("已放入“不感兴趣”");
  renderExplore();
}

function renderExplore() {
  hideAllViews();
  dom.exploreHome.hidden = false;

  dom.exploreModeTabs.forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === state.exploreMode);
  });

  if (state.exploreMode === "random") {
    dom.randomExplorePanel.hidden = false;
    dom.libraryExplorePanel.hidden = true;
    renderRandomExplore();
    return;
  }

  dom.randomExplorePanel.hidden = true;
  dom.libraryExplorePanel.hidden = false;
  renderCategoryFilters();

  const all = filteredExploreCards();
  const visible = all.slice(0, state.exploreLimit);

  dom.exploreCount.textContent = `${all.length} 条`;
  renderKnowledgeList(
    dom.exploreList,
    visible,
    "explore",
    "当前筛选条件下没有内容。可以切换到“全部”或换一个分类。"
  );

  dom.showMoreButton.hidden = visible.length >= all.length;
}

function mineCards(section) {
  const map = state.cardMap;
  let ids = [];

  if (section === "favorites") ids = [...getFavorites()];
  if (section === "read") ids = [...getRead()];
  if (section === "later") ids = [...getLater()];
  if (section === "disliked") ids = [...getDisliked()];

  return ids
    .map((id) => map.get(id))
    .filter(Boolean)
    .sort((a, b) =>
      String(b.created_at || "").localeCompare(String(a.created_at || ""))
    );
}

function setMineSection(section) {
  state.mineSection = section;
  dom.mineTabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.mine === section);
  });
  renderMine();
}

function renderMine() {
  hideAllViews();
  dom.mineHome.hidden = false;
  updateCounts();

  const labels = {
    favorites: "我的收藏",
    read: "已经读过",
    later: "稍后再看",
    disliked: "不感兴趣"
  };

  const cards = mineCards(state.mineSection);
  dom.mineListTitle.textContent = labels[state.mineSection];
  dom.mineListCount.textContent = `${cards.length} 条`;

  dom.mineTabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.mine === state.mineSection);
  });

  renderKnowledgeList(
    dom.mineList,
    cards,
    "mine",
    `“${labels[state.mineSection]}”里暂时没有内容。`
  );
}

function openDetail(cardId, origin) {
  const card = state.cardMap.get(cardId);
  if (!card) return;

  state.currentCardId = cardId;
  state.detailOrigin = origin;

  // 进入详情不立即算已读。只有主动点击“标记为已读”
  // 或随机探索中的“看完，继续探索”才改变阅读状态。
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function closeDetail() {
  state.currentCardId = null;
  dom.sourceSection.hidden = true;
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderDetail() {
  const card = state.cardMap.get(state.currentCardId);
  if (!card) {
    state.currentCardId = null;
    render();
    return;
  }

  hideAllViews();
  dom.detailView.hidden = false;
  dom.detailDock.hidden = false;

  const read = getRead();
  const favorites = getFavorites();
  const later = getLater();

  dom.cardNumber.textContent = String(
    Math.max(1, state.cards.findIndex((item) => item.id === card.id) + 1)
  ).padStart(3, "0");

  dom.categoryLabel.textContent = card.category || "综合";
  dom.readLabel.textContent = read.has(card.id) ? "已读" : "未读";
  dom.dateLabel.textContent = formatDate(card.created_at);
  dom.cardTitle.textContent = card.title;
  dom.cardLead.textContent = card.lead;
  dom.cardExplanation.textContent = card.explanation;
  dom.cardAngle.textContent = card.angle;
  dom.evidenceText.textContent = card.evidence
    ? `来源依据：${card.evidence}`
    : "这条知识暂未附带可展示的来源摘录。";
  dom.sourceLink.href = card.source_url || "#";
  dom.sourceLink.textContent =
    `查看原始来源：${card.source_name || "原文"} ↗`;

  dom.favoriteButton.textContent = favorites.has(card.id) ? "♥" : "♡";
  dom.favoriteButton.classList.toggle("saved", favorites.has(card.id));
  dom.laterButton.textContent = later.has(card.id) ? "已稍后" : "稍后";
  dom.dislikeButton.textContent = getDisliked().has(card.id)
    ? "恢复"
    : "不感兴趣";

  if (state.detailOrigin === "daily") {
    dom.completeButton.textContent = read.has(card.id)
      ? "下一条"
      : "看完，下一条";
  } else {
    dom.completeButton.textContent = read.has(card.id)
      ? "标记为未读"
      : "标记为已读";
  }
}

function nextDailyCard(currentId) {
  const remaining = dailyCards().filter((card) => card.id !== currentId);

  if (remaining.length) {
    openDetail(remaining[0].id, "daily");
  } else {
    state.currentCardId = null;
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function completeCurrent() {
  const card = state.cardMap.get(state.currentCardId);
  if (!card) return;

  const read = getRead();

  if (state.detailOrigin === "daily") {
    read.add(card.id);
    saveSet(STORE.read, read);
    recordProgress(card.id, "read");
    showToast("已读完");
    nextDailyCard(card.id);
    return;
  }

  if (read.has(card.id)) {
    read.delete(card.id);
    showToast("已标记为未读");
  } else {
    read.add(card.id);
    recordProgress(card.id, "read");
    showToast("已标记为已读");
  }

  saveSet(STORE.read, read);
  renderDetail();
  updateCounts();
}

function toggleFavorite() {
  const cardId = state.currentCardId;
  if (!cardId) return;

  const favorites = getFavorites();

  if (favorites.has(cardId)) {
    favorites.delete(cardId);
    showToast("已取消收藏");
  } else {
    favorites.add(cardId);
    recordProgress(cardId, "favorite");
    showToast("已收藏");
  }

  saveSet(STORE.favorites, favorites);
  renderDetail();
  updateCounts();
}

function toggleLater() {
  const cardId = state.currentCardId;
  if (!cardId) return;

  const later = getLater();

  if (later.has(cardId)) {
    later.delete(cardId);
    showToast("已移出稍后再看");
  } else {
    later.add(cardId);
    showToast("已加入稍后再看");
  }

  saveSet(STORE.later, later);

  if (state.detailOrigin === "daily" && later.has(cardId)) {
    nextDailyCard(cardId);
  } else {
    renderDetail();
    updateCounts();
  }
}

function dislikeCurrent() {
  const cardId = state.currentCardId;
  if (!cardId) return;

  const disliked = getDisliked();

  if (disliked.has(cardId)) {
    disliked.delete(cardId);
    saveSet(STORE.disliked, disliked);
    showToast("已恢复到知识库");
    renderDetail();
    return;
  }

  const confirmed = window.confirm(
    "确定不再在每日推荐和随机探索中显示这条知识吗？"
  );
  if (!confirmed) return;

  disliked.add(cardId);
  saveSet(STORE.disliked, disliked);

  const favorites = getFavorites();
  favorites.delete(cardId);
  saveSet(STORE.favorites, favorites);

  const later = getLater();
  later.delete(cardId);
  saveSet(STORE.later, later);

  showToast("已放入“不感兴趣”");
  state.currentCardId = null;
  render();
}

function showSource() {
  dom.sourceSection.hidden = false;
  dom.sourceSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function showToast(message) {
  dom.toast.textContent = message;
  dom.toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    dom.toast.classList.remove("show");
  }, 1900);
}

function normalizeEndpoint(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

function triggerSettings() {
  return {
    endpoint: normalizeEndpoint(localStorage.getItem(STORE.triggerEndpoint)),
    secret: localStorage.getItem(STORE.triggerSecret) || ""
  };
}

function updateTriggerState() {
  const settings = triggerSettings();
  const ready = Boolean(settings.endpoint && settings.secret);
  dom.triggerSettingsState.textContent = ready ? "已连接" : "未连接";
  dom.triggerSettingsState.classList.toggle("ready", ready);
}



function readActiveRun() {
  return readJson(STORE.activeRun, null);
}

function saveActiveRun(run) {
  writeJson(STORE.activeRun, run);
}

function clearActiveRun() {
  localStorage.removeItem(STORE.activeRun);
}

function setSupplyTaskState(
  taskState,
  title,
  detail,
  htmlUrl = ""
) {
  dom.supplyTaskCard.dataset.state = taskState;
  dom.supplyTaskTitle.textContent = title;
  dom.supplyTaskDetail.textContent = detail;

  const icons = {
    idle: "○",
    queued: "…",
    running: "↻",
    publishing: "↑",
    success: "✓",
    cooldown: "◷",
    error: "!"
  };
  dom.supplyTaskCard.querySelector(".supply-task-icon").textContent =
    icons[taskState] || "○";

  if (htmlUrl) {
    dom.supplyTaskLink.href = htmlUrl;
    dom.supplyTaskLink.hidden = false;
  } else {
    dom.supplyTaskLink.hidden = true;
    dom.supplyTaskLink.removeAttribute("href");
  }

  writeJson(STORE.lastSupplyState, {
    taskState,
    title,
    detail,
    htmlUrl,
    savedAt: new Date().toISOString()
  });
}

function restoreSupplyTaskState() {
  const saved = readJson(STORE.lastSupplyState, null);
  if (!saved) return;
  setSupplyTaskState(
    saved.taskState || "idle",
    saved.title || "当前没有生成任务",
    saved.detail || "",
    saved.htmlUrl || ""
  );
}

function setReplenishButtonBusy(busy, label = "") {
  state.loadingSupply = busy;
  dom.replenishButton.disabled = busy;
  dom.replenishButton.textContent =
    label || (busy ? "后台处理中……" : "立即补充知识库");
}

function elapsedText(startedAt) {
  const started = new Date(startedAt || Date.now()).getTime();
  const seconds = Math.max(0, Math.floor((Date.now() - started) / 1000));
  if (seconds < 60) return `${seconds}秒`;
  return `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
}

async function fetchWorkerResult(runId) {
  return workerRequest(`/result?run_id=${encodeURIComponent(runId)}`);
}

async function refreshPublishedCardsInBackground(expectedCount) {
  for (let attempt = 0; attempt < 18; attempt += 1) {
    await sleep(attempt === 0 ? 4000 : 7000);
    try {
      const payload = await fetchCardsPayload();
      const count = Array.isArray(payload.cards) ? payload.cards.length : 0;
      if (count >= expectedCount) {
        applyCardsPayload(payload);
        await refreshPoolStatus();
        return;
      }
    } catch {
      // Pages may still be publishing.
    }
  }
}

function applyImmediateRunResult(result) {
  const lastRun = result?.last_run || result?.run || null;
  if (!lastRun) return 0;

  const added = Number(lastRun.added || 0);
  const approved = Number(result.approved_cards || state.cards.length + added);
  const pending = Number(result.pending_candidates || 0);

  applyPoolStatus({
    approved_cards: approved,
    pending_candidates: pending,
    last_run: lastRun
  });

  setSupplyTaskState(
    "publishing",
    `生成完成：新增 ${added} 条`,
    "GitHub 已保存结果，网页正在发布新知识；现在不需要再次点击。",
    lastRun.html_url || ""
  );

  refreshPublishedCardsInBackground(approved).then(() => {
    setSupplyTaskState(
      "success",
      `本次新增 ${added} 条知识`,
      `正式知识库现有约 ${approved} 条，候选池剩余 ${pending} 条。`
    );
  });

  return added;
}

async function resumeSupplyTracking() {
  const settings = triggerSettings();
  if (!settings.endpoint || !settings.secret) return;

  const active = readActiveRun();
  if (active?.runId) {
    setReplenishButtonBusy(true, "后台处理中……");
    pollSupply(
      active.runId,
      active.beforeCount || state.cards.length,
      active.htmlUrl || "",
      active.startedAt || new Date().toISOString()
    ).catch((error) => {
      setSupplyTaskState(
        "error",
        "无法继续读取任务状态",
        error.message || "稍后重新打开数据页即可。"
      );
      setReplenishButtonBusy(false);
    });
    return;
  }

  try {
    const latest = await workerRequest("/latest");
    if (
      latest?.run_id &&
      ["queued", "in_progress", "waiting", "requested", "pending"]
        .includes(latest.status)
    ) {
      const run = {
        runId: latest.run_id,
        beforeCount: state.cards.length,
        startedAt: latest.created_at || new Date().toISOString(),
        htmlUrl: latest.html_url || ""
      };
      saveActiveRun(run);
      setReplenishButtonBusy(true, "后台处理中……");
      pollSupply(
        run.runId,
        run.beforeCount,
        run.htmlUrl,
        run.startedAt
      ).catch(() => {});
    }
  } catch {
    // Background status recovery is helpful, not required for reading.
  }
}

function formatCompactNumber(value) {
  const number = Number(value || 0);
  if (number >= 1000000) return `${(number / 1000000).toFixed(1)}M`;
  if (number >= 1000) return `${(number / 1000).toFixed(1)}K`;
  return String(number);
}

async function fetchPoolStatus() {
  const response = await fetch(`data/pool_status.json?t=${Date.now()}`, {
    cache: "no-store"
  });
  if (!response.ok) throw new Error("读取后台库存状态失败");
  return response.json();
}

function applyPoolStatus(payload) {
  const lastRun = payload?.last_run || null;
  const lastHarvest = payload?.last_harvest || null;

  dom.pendingCandidateCount.textContent = String(
    payload?.pending_candidates || 0
  );

  if (lastHarvest) {
    dom.harvestFetchedCount.textContent = String(
      lastHarvest.fetched || 0
    );
    dom.harvestAddedCount.textContent = String(
      lastHarvest.added || 0
    );
    dom.harvestPoolAfter.textContent = String(
      lastHarvest.pool_after || 0
    );
    dom.harvestStatusNote.textContent = lastHarvest.message ||
      `上次囤货运行 ${lastHarvest.passes || 0} 轮，` +
      `抓取 ${lastHarvest.fetched || 0} 条，` +
      `实际入池 ${lastHarvest.added || 0} 条。`;
  } else {
    dom.harvestFetchedCount.textContent = "—";
    dom.harvestAddedCount.textContent = "—";
    dom.harvestPoolAfter.textContent = "—";
    dom.harvestStatusNote.textContent =
      "候选池会自动补充，不调用 AI。";
  }

  if (!lastRun) {
    dom.lastAddedCount.textContent = "0";
    dom.lastPassRate.textContent = "—";
    dom.lastTokenCount.textContent = "—";
    dom.lastProcessedCount.textContent = "—";
    dom.poolStatusTime.textContent = "尚未运行";
    dom.poolStatusNote.textContent =
      "第一次批量生成后，这里会显示通过率和消耗。";
    return;
  }

  dom.lastAddedCount.textContent = String(lastRun.added || 0);
  dom.lastPassRate.textContent =
    `${Number(lastRun.pass_rate || 0).toFixed(1)}%`;
  dom.lastProcessedCount.textContent = String(lastRun.processed || 0);

  const totalTokens =
    Number(lastRun.input_tokens || 0) +
    Number(lastRun.output_tokens || 0);
  dom.lastTokenCount.textContent = formatCompactNumber(totalTokens);
  dom.poolStatusTime.textContent = formatDate(
    payload.updated_at || lastRun.finished_at
  );

  if (lastRun.message) {
    dom.poolStatusNote.textContent = lastRun.message;
  } else {
    dom.poolStatusNote.textContent =
      `上次处理 ${lastRun.processed || 0} 个候选，` +
      `形成 ${lastRun.proposals || 0} 个提案，` +
      `审核 ${lastRun.reviewed || 0} 条，` +
      `最终新增 ${lastRun.added || 0} 条。`;
  }
}

async function refreshPoolStatus() {
  try {
    const payload = await fetchPoolStatus();
    applyPoolStatus(payload);
    return payload;
  } catch {
    return null;
  }
}

function openDataDialog() {
  const read = getRead();
  const favorites = getFavorites();

  dom.totalCards.textContent = String(state.cards.length);
  dom.totalUnread.textContent = String(
    state.cards.filter((card) => !read.has(card.id)).length
  );
  dom.totalFavorites.textContent = String(favorites.size);

  const settings = triggerSettings();
  dom.triggerEndpointInput.value = settings.endpoint;
  dom.triggerSecretInput.value = settings.secret;
  updateTriggerState();
  refreshPoolStatus();

  dom.dataDialog.showModal();
}

function saveTriggerSettings() {
  const endpoint = normalizeEndpoint(dom.triggerEndpointInput.value);
  const secret = dom.triggerSecretInput.value.trim();

  if (!endpoint.startsWith("https://")) {
    showToast("服务地址需要以 https:// 开头");
    return;
  }

  if (secret.length < 20) {
    showToast("个人触发码至少需要20位");
    return;
  }

  localStorage.setItem(STORE.triggerEndpoint, endpoint);
  localStorage.setItem(STORE.triggerSecret, secret);
  updateTriggerState();
  showToast("后台设置已保存");
}

async function workerRequest(path, options = {}) {
  const settings = triggerSettings();

  const response = await fetch(`${settings.endpoint}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Trigger-Key": settings.secret,
      ...(options.headers || {})
    }
  });

  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }

  if (!response.ok) {
    const error = new Error(payload.message || `请求失败（${response.status}）`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  return payload;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchCardsPayload() {
  const response = await fetch(`data/cards.json?t=${Date.now()}`, {
    cache: "no-store"
  });

  if (!response.ok) throw new Error("读取知识库失败");
  return response.json();
}

function applyCardsPayload(payload) {
  state.cards = Array.isArray(payload.cards) ? payload.cards : [];
  state.cardMap = new Map(state.cards.map((card) => [card.id, card]));
  dom.updateStatus.textContent =
    `知识库 ${state.cards.length} 条 · ${formatDate(payload.updated_at)}`;

  ensureDailyRecord();
  render();
}

async function pollSupply(
  runId,
  beforeCount,
  htmlUrl = "",
  startedAt = new Date().toISOString()
) {
  saveActiveRun({
    runId,
    beforeCount,
    htmlUrl,
    startedAt
  });

  setReplenishButtonBusy(true, "后台处理中……");

  for (let attempt = 0; attempt < 90; attempt += 1) {
    setSupplyTaskState(
      attempt < 2 ? "queued" : "running",
      attempt < 2 ? "任务已提交" : "正在筛选并审核知识",
      `后台已运行 ${elapsedText(startedAt)}。离开页面也不会中断。`,
      htmlUrl
    );

    const status = await workerRequest(
      `/status?run_id=${encodeURIComponent(runId)}`
    );

    if (status.status === "completed") {
      if (status.conclusion !== "success") {
        clearActiveRun();
        setReplenishButtonBusy(false);
        setSupplyTaskState(
          "error",
          "本次后台任务失败",
          "GitHub Actions 已结束，但没有成功完成。可打开任务查看日志。",
          status.html_url || htmlUrl
        );
        throw new Error("后台补充任务没有成功");
      }

      // Read the committed result directly through the Worker. This does not
      // wait for GitHub Pages deployment or browser cache.
      for (let resultAttempt = 0; resultAttempt < 24; resultAttempt += 1) {
        try {
          const result = await fetchWorkerResult(runId);
          clearActiveRun();
          setReplenishButtonBusy(false);
          return applyImmediateRunResult(result);
        } catch (error) {
          if (error.status !== 202) throw error;
          await sleep(3000);
        }
      }

      clearActiveRun();
      setReplenishButtonBusy(false);
      setSupplyTaskState(
        "publishing",
        "任务已经完成",
        "结果已提交，网页正在发布。稍后重新打开数据页即可看到新增数量。",
        status.html_url || htmlUrl
      );
      return 0;
    }

    await sleep(6000);
  }

  throw new Error("后台仍在运行，稍后重新打开数据页会自动继续跟踪");
}

async function startOrJoinSupplyRun(payload) {
  const runId = payload?.run_id;
  if (!runId) throw new Error("没有获得任务编号");

  const run = {
    runId,
    beforeCount: state.cards.length,
    startedAt: payload.created_at || new Date().toISOString(),
    htmlUrl: payload.html_url || ""
  };
  saveActiveRun(run);

  return pollSupply(
    run.runId,
    run.beforeCount,
    run.htmlUrl,
    run.startedAt
  );
}

async function showCooldownState(error) {
  const seconds = Math.max(1, Number(error.payload?.retry_after || 60));
  const until = Date.now() + seconds * 1000;

  setReplenishButtonBusy(true, "冷却中……");

  try {
    const latest = await workerRequest("/latest");
    if (latest?.run_id) {
      try {
        const result = await fetchWorkerResult(latest.run_id);
        applyImmediateRunResult(result);
      } catch {
        setSupplyTaskState(
          "cooldown",
          "刚刚已经运行过",
          `为避免重复消耗，约 ${Math.ceil(seconds / 60)} 分钟后才能再次启动。`,
          latest.html_url || ""
        );
      }
    }
  } catch {
    setSupplyTaskState(
      "cooldown",
      "刚刚已经运行过",
      `为避免重复消耗，约 ${Math.ceil(seconds / 60)} 分钟后才能再次启动。`
    );
  }

  const timer = window.setInterval(() => {
    const remaining = Math.max(0, Math.ceil((until - Date.now()) / 1000));
    if (remaining <= 0) {
      window.clearInterval(timer);
      setReplenishButtonBusy(false);
      setSupplyTaskState(
        "idle",
        "可以再次补充",
        "候选原料会由后台定时补充，通常无需连续点击。"
      );
      return;
    }
    dom.replenishButton.textContent =
      `约 ${Math.ceil(remaining / 60)} 分钟后可重试`;
  }, 1000);
}

async function replenishKnowledge() {
  if (state.loadingSupply) {
    showToast("已有任务正在处理，不需要重复点击");
    return;
  }

  const settings = triggerSettings();
  if (!settings.endpoint || !settings.secret) {
    showToast("先保存后台服务地址和个人触发码");
    return;
  }

  setReplenishButtonBusy(true, "正在提交……");
  setSupplyTaskState(
    "queued",
    "正在提交后台任务",
    "只会启动一次；重复点击不会生成多个任务。"
  );

  try {
    const payload = await workerRequest("/trigger", {
      method: "POST",
      body: JSON.stringify({ source: "web" })
    });

    const added = await startOrJoinSupplyRun(payload);
    showToast(
      added ? `本次新增 ${added} 条知识` : "本轮没有筛出新知识"
    );
  } catch (error) {
    if (error.status === 409 && error.payload?.run_id) {
      setSupplyTaskState(
        "running",
        "已有任务正在运行",
        "已自动接入现有任务，不会重复调用 AI。",
        error.payload.html_url || ""
      );
      try {
        const added = await startOrJoinSupplyRun(error.payload);
        showToast(
          added ? `本次新增 ${added} 条知识` : "本轮没有筛出新知识"
        );
      } catch (nestedError) {
        setSupplyTaskState(
          "error",
          "读取任务状态失败",
          nestedError.message || "稍后重新打开数据页即可继续。"
        );
        setReplenishButtonBusy(false);
      }
    } else if (error.status === 429) {
      await showCooldownState(error);
      showToast("刚刚已经运行过，不需要重复生成");
    } else {
      clearActiveRun();
      setReplenishButtonBusy(false);
      setSupplyTaskState(
        "error",
        "后台补充失败",
        error.message || "稍后再试。"
      );
      showToast(error.message || "后台补充失败");
    }
  }
}


function exportData() {
  const payload = {
    version: STORE.backupVersion,
    exportedAt: new Date().toISOString(),
    favorites: [...getFavorites()],
    read: [...getRead()],
    later: [...getLater()],
    disliked: [...getDisliked()],
    daily: readJson(STORE.daily, null),
    triggerEndpoint: localStorage.getItem(STORE.triggerEndpoint) || ""
  };

  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json"
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `拾光个人记录_${localDateKey()}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
  showToast("个人记录已导出");
}

async function importData(file) {
  if (!file) return;

  try {
    const payload = JSON.parse(await file.text());

    if (Array.isArray(payload.favorites)) {
      writeJson(STORE.favorites, payload.favorites);
    }
    if (Array.isArray(payload.read)) {
      writeJson(STORE.read, payload.read);
    }
    if (Array.isArray(payload.later)) {
      writeJson(STORE.later, payload.later);
    }
    if (Array.isArray(payload.disliked)) {
      writeJson(STORE.disliked, payload.disliked);
    }
    if (payload.daily && Array.isArray(payload.daily.ids)) {
      writeJson(STORE.daily, payload.daily);
    }

    ensureDailyRecord();
    render();
    dom.dataDialog.close();
    showToast("个人记录已导入");
  } catch {
    showToast("无法读取这个备份文件");
  } finally {
    dom.importInput.value = "";
  }
}

async function loadCards() {
  try {
    const payload = await fetchCardsPayload();
    applyCardsPayload(payload);
    dom.loadingState.hidden = true;
  } catch {
    dom.loadingState.textContent = "暂时无法读取知识库，请稍后刷新。";
    dom.updateStatus.textContent = "读取失败";
  }
}

function bindEvents() {
  dom.brandButton.addEventListener("click", () => setView("daily"));

  dom.mainTabs.forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });

  dom.exploreModeTabs.forEach((button) => {
    button.addEventListener("click", () => {
      setExploreMode(button.dataset.mode);
    });
  });

  dom.randomSkipButton.addEventListener("click", () => {
    advanceRandomCard({ putBack: true });
  });

  dom.randomCompleteButton.addEventListener("click", () => {
    advanceRandomCard({ markRead: true });
    showToast("已读完，继续探索");
  });

  dom.randomFavoriteButton.addEventListener("click", toggleRandomFavorite);
  dom.randomLaterButton.addEventListener("click", toggleRandomLater);
  dom.randomDislikeButton.addEventListener("click", dislikeRandomCard);

  dom.searchInput.addEventListener("input", () => {
    state.exploreLimit = PAGE_SIZE;
    renderExplore();
  });

  dom.statusFilters.forEach((button) => {
    button.addEventListener("click", () => {
      state.exploreStatus = button.dataset.status;
      state.exploreLimit = PAGE_SIZE;
      dom.statusFilters.forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderExplore();
    });
  });

  dom.sortFilters.forEach((button) => {
    button.addEventListener("click", () => {
      state.exploreSort = button.dataset.sort;
      state.exploreLimit = PAGE_SIZE;
      dom.sortFilters.forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderExplore();
    });
  });

  dom.showMoreButton.addEventListener("click", () => {
    state.exploreLimit += PAGE_SIZE;
    renderExplore();
  });

  dom.summaryCards.forEach((button) => {
    button.addEventListener("click", () => setMineSection(button.dataset.mine));
  });
  dom.mineTabs.forEach((button) => {
    button.addEventListener("click", () => setMineSection(button.dataset.mine));
  });

  dom.backButton.addEventListener("click", closeDetail);
  dom.sourceButton.addEventListener("click", showSource);
  dom.closeSourceButton.addEventListener("click", () => {
    dom.sourceSection.hidden = true;
  });
  dom.completeButton.addEventListener("click", completeCurrent);
  dom.favoriteButton.addEventListener("click", toggleFavorite);
  dom.laterButton.addEventListener("click", toggleLater);
  dom.dislikeButton.addEventListener("click", dislikeCurrent);

  dom.dataButton.addEventListener("click", openDataDialog);
  dom.closeDataButton.addEventListener("click", () => dom.dataDialog.close());
  dom.dataDialog.addEventListener("click", (event) => {
    if (event.target === dom.dataDialog) dom.dataDialog.close();
  });

  dom.exportButton.addEventListener("click", exportData);
  dom.importInput.addEventListener("change", () => {
    importData(dom.importInput.files[0]);
  });

  dom.resetDailyButton.addEventListener("click", () => {
    ensureDailyRecord(true);
    dom.dataDialog.close();
    setView("daily");
    showToast("今日推荐已重新抽取");
  });

  dom.saveTriggerSettingsButton.addEventListener("click", saveTriggerSettings);
  dom.replenishButton.addEventListener("click", replenishKnowledge);

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
  bindEvents();
  renderCategoryFilters();
  updateTriggerState();
  restoreSupplyTaskState();
  loadCards().then(() => {
    resumeSupplyTracking();
  });
  refreshPoolStatus();
  registerServiceWorker();
}

init();
