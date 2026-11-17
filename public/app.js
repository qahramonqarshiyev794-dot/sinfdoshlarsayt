const views = ["request-view", "user-login-view", "admin-login-view", "pending-view", "viewer-view", "admin-view"];
const requestKeyName = "sinfdoshlar_access_key";
const requestEmailName = "sinfdoshlar_access_email";
let activeYoutubePlayer = null;
let youtubeApiPromise = null;
let userSessionTimer = null;

function showView(name) {
  for (const id of views) document.getElementById(id).hidden = id !== name;
}

function showNotice(message, isError = false) {
  const notice = document.getElementById("notice");
  notice.textContent = message;
  notice.classList.toggle("notice-error", isError);
  notice.hidden = false;
}

async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
  } catch {
    throw new Error("Server bilan bog‘lanib bo‘lmadi. Internetga ulanishni tekshiring.");
  }
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error("Serverdan tushunarsiz javob olindi.");
  }
  if (!response.ok) throw new Error(result.error || "So‘rov bajarilmadi.");
  return result;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatDate(timestamp) {
  return new Intl.DateTimeFormat("uz-UZ", { dateStyle: "medium" }).format(new Date(timestamp * 1000));
}

function groupedVideos(videos) {
  const groups = new Map();
  for (const video of videos) {
    const category = video.category || "Umumiy";
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(video);
  }
  return groups;
}

function loadYoutubeApi() {
  if (window.YT && window.YT.Player) return Promise.resolve(window.YT);
  if (youtubeApiPromise) return youtubeApiPromise;

  youtubeApiPromise = new Promise((resolve, reject) => {
    const previousCallback = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      if (typeof previousCallback === "function") previousCallback();
      resolve(window.YT);
    };
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.onerror = () => reject(new Error("YouTube pleyerini yuklab bo‘lmadi."));
    document.head.append(script);
  });
  return youtubeApiPromise;
}

function isYoutubeEmbed(url) {
  try {
    const parsed = new URL(url);
    return parsed.hostname === "www.youtube.com" && parsed.pathname.startsWith("/embed/");
  } catch {
    return false;
  }
}

function setPlayButton(button, isPlaying) {
  button.textContent = isPlaying ? "⏸" : "▶";
  button.setAttribute("aria-label", isPlaying ? "Pauza" : "Ijro etish");
}

function openVideo(video, playlist = [], videoIndex = -1) {
  const player = document.getElementById("video-player");
  const content = document.getElementById("player-content");
  const playButton = document.getElementById("player-play-toggle");
  const previousButton = document.getElementById("player-previous");
  const nextButton = document.getElementById("player-next");
  if (activeYoutubePlayer) {
    activeYoutubePlayer.destroy();
    activeYoutubePlayer = null;
  }
  content.replaceChildren();
  playButton.hidden = true;
  playButton.onclick = null;
  setPlayButton(playButton, false);
  previousButton.disabled = videoIndex <= 0;
  nextButton.disabled = videoIndex < 0 || videoIndex >= playlist.length - 1;
  previousButton.onclick = () => {
    if (videoIndex > 0) openVideo(playlist[videoIndex - 1], playlist, videoIndex - 1);
  };
  nextButton.onclick = () => {
    if (videoIndex >= 0 && videoIndex < playlist.length - 1) {
      openVideo(playlist[videoIndex + 1], playlist, videoIndex + 1);
    }
  };
  document.getElementById("player-title").textContent = video.title;
  player.hidden = false;
  player.scrollIntoView({ behavior: "smooth", block: "center" });

  let media;
  if (video.type === "file") {
    media = element("video", "online-video");
    media.playsInline = true;
    media.preload = "metadata";
    media.controls = true;
    media.controlsList = "nodownload noremoteplayback";
    media.disablePictureInPicture = true;
    media.disableRemotePlayback = true;
    media.draggable = false;
    media.addEventListener("contextmenu", event => event.preventDefault());
    const source = element("source");
    source.src = video.playUrl;
    media.append(source);
  } else {
    media = element("iframe", "online-embed");
    media.title = video.title;
    media.referrerPolicy = "strict-origin-when-cross-origin";
    media.allow = "autoplay; fullscreen; encrypted-media; picture-in-picture";
    media.allowFullscreen = true;
    media.loading = "eager";
    if (isYoutubeEmbed(video.playUrl)) {
      const source = new URL(video.playUrl);
      source.searchParams.set("origin", window.location.origin);
      media.src = source.toString();
      playButton.hidden = false;
      playButton.disabled = true;
      playButton.onclick = () => {
        const state = activeYoutubePlayer.getPlayerState();
        if (state === window.YT.PlayerState.PLAYING) activeYoutubePlayer.pauseVideo();
        else activeYoutubePlayer.playVideo();
      };
      loadYoutubeApi().then(YT => {
        if (!media.isConnected) return;
        activeYoutubePlayer = new YT.Player(media, {
          events: {
            onReady: () => {
              playButton.disabled = false;
            },
            onStateChange: event => {
              setPlayButton(playButton, event.data === YT.PlayerState.PLAYING);
            },
            onError: () => showNotice("YouTube videosini ijro etib bo‘lmadi.", true),
          },
        });
      }).catch(error => {
        if (!media.isConnected) return;
        playButton.hidden = true;
        showNotice("Tashqi video pleyerini ochib bo‘lmadi. Birozdan so‘ng qayta urinib ko‘ring.", true);
      });
    } else {
      media.src = video.playUrl;
    }
  }
  content.replaceChildren(media);
}

async function loadVideos(targetId, isAdmin = false) {
  const target = document.getElementById(targetId);
  target.replaceChildren();
  const { videos, categories } = await api("/api/videos");
  if (!isAdmin) {
    document.getElementById("player-content").replaceChildren();
    document.getElementById("video-player").hidden = true;
  } else {
    const datalist = document.getElementById("video-categories");
    datalist.replaceChildren(...categories.map(category => {
      const option = element("option");
      option.value = category;
      return option;
    }));
  }

  if (!videos.length) {
    target.append(element("p", "empty-state", "Hozircha video qo‘shilmagan."));
    return;
  }

  for (const [category, categoryVideos] of groupedVideos(videos)) {
    const section = element("details", isAdmin ? "admin-video-section" : "video-section");
    const heading = element("summary", "video-section-title");
    heading.append(
      element("span", "category-name", category),
      element("span", "category-count", String(categoryVideos.length)),
    );
    section.append(heading);
    const list = element("div", isAdmin ? "admin-video-list" : "video-grid");
    categoryVideos.forEach(video => {
      const card = element("article", isAdmin ? "admin-video-row" : "video-card");
      const icon = element("span", "video-icon", "▶");
      const details = element("div", "video-details");
      details.append(element("h3", "", video.title));
      if (isAdmin) {
        const source = element("a", "small-link", "Manbani ochish ↗");
        source.href = video.url;
        source.target = "_blank";
        source.rel = "noopener noreferrer";
        card.append(icon, details, source);
        const remove = element("button", "remove-button", "O‘chirish");
        remove.type = "button";
        remove.addEventListener("click", async () => {
          if (!window.confirm(`${video.title} nomli videoni o‘chirasizmi?`)) return;
          try {
            await api(`/api/videos/${video.id}`, { method: "POST" });
            await loadVideos("admin-videos", true);
          } catch (error) {
            showNotice(error.message, true);
          }
        });
        card.append(remove);
      } else if (video.playUrl) {
        const play = element("button", "video-link", "▶");
        play.type = "button";
        play.setAttribute("aria-label", `Videoni ijro etish: ${video.title}`);
        play.addEventListener("click", () => openVideo(video, videos, videos.indexOf(video)));
        card.append(details, play);
      } else {
        card.append(details);
      }
      list.append(card);
    });
    section.append(list);
    target.append(section);
  }
}

async function loadRequests() {
  const target = document.getElementById("admin-requests");
  const { requests } = await api("/api/admin/requests");
  target.replaceChildren();
  document.getElementById("request-count").textContent = String(requests.filter(item => item.status === "pending").length);
  if (!requests.length) {
    target.append(element("p", "empty-state", "Hozircha so‘rov yo‘q."));
    return;
  }
  for (const request of requests) {
    const row = element("article", "request-row");
    const info = element("div", "request-info");
    info.append(
      element("strong", "", request.name),
      element("span", "", request.email || "Eski so‘rov"),
      element("span", "", formatDate(request.createdAt)),
    );
    const state = element("span", `request-state state-${request.status}`, {
      pending: "Kutilmoqda",
      approved: "Tasdiqlangan",
      rejected: "Rad etilgan",
    }[request.status] || request.status);
    row.append(info, state);
    if (request.status === "pending") {
      const actions = element("div", "request-actions");
      const approve = element("button", "approve-button", "Tasdiqlash");
      approve.type = "button";
      approve.addEventListener("click", () => updateRequest(request.id, "approve"));
      const reject = element("button", "reject-button", "Rad etish");
      reject.type = "button";
      reject.addEventListener("click", () => updateRequest(request.id, "reject"));
      actions.append(approve, reject);
      row.append(actions);
    }
    target.append(row);
  }
}

async function updateRequest(id, action) {
  try {
    await api(`/api/admin/requests/${id}/${action}`, { method: "POST" });
    await Promise.all([loadRequests(), loadUsers()]);
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function loadUsers() {
  const target = document.getElementById("admin-users");
  const { users } = await api("/api/admin/users");
  target.replaceChildren();
  document.getElementById("user-count").textContent = String(users.length);
  if (!users.length) {
    target.append(element("p", "empty-state", "Tasdiqlangan foydalanuvchi yo‘q."));
    return;
  }
  for (const user of users) {
    const row = element("article", "user-row");
    const info = element("div", "user-info");
    info.append(element("strong", "", user.name), element("span", "", user.email));
    const state = element("span", `user-state ${user.blocked ? "user-state-blocked" : "user-state-active"}`, user.blocked ? "Bloklangan" : "Faol");
    const toggle = element("button", user.blocked ? "approve-button user-toggle" : "reject-button user-toggle", user.blocked ? "Blokdan chiqarish" : "Bloklash");
    toggle.type = "button";
    toggle.addEventListener("click", () => setUserBlocked(user.email, !user.blocked, toggle));
    row.append(info, state, toggle);
    target.append(row);
  }
}

async function setUserBlocked(email, blocked, button) {
  const confirmed = window.confirm(blocked
    ? `Foydalanuvchini bloklaysizmi? ${email} darhol saytga kira olmaydi.`
    : `Foydalanuvchini blokdan chiqarasizmi? ${email} qayta kira oladi.`);
  if (!confirmed) return;
  button.disabled = true;
  try {
    await api("/api/admin/users/status", {
      method: "POST",
      body: JSON.stringify({ email, blocked }),
    });
    await loadUsers();
    showNotice(blocked ? "Foydalanuvchi bloklandi." : "Foydalanuvchi blokdan chiqarildi.");
  } catch (error) {
    button.disabled = false;
    showNotice(error.message, true);
  }
}

async function enterViewer() {
  showView("viewer-view");
  await loadVideos("video-list");
  if (userSessionTimer) clearInterval(userSessionTimer);
  userSessionTimer = setInterval(async () => {
    try {
      const session = await api("/api/me");
      if (session.role !== "user") {
        clearInterval(userSessionTimer);
        userSessionTimer = null;
        document.getElementById("player-content").replaceChildren();
        document.getElementById("video-player").hidden = true;
        showView("request-view");
        showNotice("Kirish muddati tugadi yoki hisob bloklandi. Qayta kiring.", true);
      }
    } catch {
      // Vaqtinchalik tarmoq uzilishi joriy ko‘rinishni o‘zgartirmaydi.
    }
  }, 15000);
}

async function restoreSession() {
  try {
    const session = await api("/api/me");
    if (session.role === "admin") {
      showView("admin-view");
      await Promise.all([loadRequests(), loadVideos("admin-videos", true), loadUsers()]);
      return true;
    }
    if (session.role === "user") {
      await enterViewer();
      return true;
    }
  } catch (error) {
    showNotice(error.message, true);
  }
  return false;
}

function startStatusPolling(key) {
  showView("pending-view");
  const status = document.getElementById("pending-status");
  const loginButton = document.getElementById("pending-login");
  loginButton.hidden = true;
  status.textContent = "Holat tekshirilmoqda…";
  let stopped = false;
  const poll = async () => {
    if (stopped) return;
    try {
      const result = await api(`/api/access/status?key=${encodeURIComponent(key)}`);
      if (result.status === "approved") {
        stopped = true;
        localStorage.removeItem(requestKeyName);
        document.getElementById("user-email").value = localStorage.getItem(requestEmailName) || "";
        status.textContent = "So‘rov ma’qullandi. Endi elektron pochta manzilingiz va parolingiz bilan kiring.";
        loginButton.hidden = false;
      } else if (result.status === "rejected") {
        stopped = true;
        status.textContent = "So‘rovingiz rad etildi. Boshqa elektron pochta manzili bilan qayta so‘rov yuborishingiz mumkin.";
      } else {
        status.textContent = "Boshqaruvchi tasdig‘i kutilmoqda…";
        window.setTimeout(poll, 5000);
      }
    } catch (error) {
      stopped = true;
      status.textContent = error.message;
    }
  };
  poll();
}

document.getElementById("request-form").addEventListener("submit", async event => {
  event.preventDefault();
  const status = document.getElementById("request-status");
  const button = event.currentTarget.querySelector("button[type=submit]");
  const formData = new FormData(event.currentTarget);
  const password = formData.get("password");
  status.textContent = "";
  status.classList.remove("error");
  if (password !== formData.get("passwordConfirm")) {
    status.textContent = "Kiritilgan parollar bir-biriga mos emas.";
    status.classList.add("error");
    return;
  }
  button.disabled = true;
  try {
    const result = await api("/api/register", {
      method: "POST",
      body: JSON.stringify({
        name: formData.get("name"),
        email: formData.get("email"),
        password,
      }),
    });
    localStorage.setItem(requestKeyName, result.key);
    localStorage.setItem(requestEmailName, result.email);
    startStatusPolling(result.key);
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  } finally {
    button.disabled = false;
  }
});

document.getElementById("user-login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const errorBox = document.getElementById("user-login-error");
  errorBox.textContent = "";
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    await api("/api/user/login", { method: "POST", body: JSON.stringify(values) });
    localStorage.removeItem(requestEmailName);
    await restoreSession();
  } catch (error) {
    errorBox.textContent = error.message;
  }
});

document.getElementById("admin-login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const errorBox = document.getElementById("login-error");
  errorBox.textContent = "";
  try {
    const values = Object.fromEntries(new FormData(event.currentTarget));
    await api("/api/admin/login", { method: "POST", body: JSON.stringify(values) });
    await restoreSession();
  } catch (error) {
    errorBox.textContent = error.message;
  }
});

document.getElementById("admin-password-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.getElementById("admin-password-message");
  message.textContent = "";
  message.classList.remove("error");
  try {
    const values = Object.fromEntries(new FormData(form));
    const result = await api("/api/admin/change-password", {
      method: "POST",
      body: JSON.stringify(values),
    });
    form.reset();
    message.textContent = result.message;
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
});

document.getElementById("video-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.getElementById("video-message");
  try {
    const values = Object.fromEntries(new FormData(form));
    await api("/api/videos", { method: "POST", body: JSON.stringify(values) });
    form.reset();
    message.classList.remove("error");
    message.textContent = "Video muvaffaqiyatli qo‘shildi.";
    showNotice("Video muvaffaqiyatli qo‘shildi.");
    try {
      await loadVideos("admin-videos", true);
    } catch (error) {
      showNotice(`Video saqlandi, lekin ro‘yxat yangilanmadi: ${error.message}`, true);
    }
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
});

document.querySelectorAll("[data-toggle-password]").forEach(button => {
  button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.togglePassword);
    input.type = input.type === "password" ? "text" : "password";
    button.textContent = input.type === "password" ? "Ko‘rsatish" : "Yashirish";
  });
});

document.getElementById("show-admin").addEventListener("click", () => showView("admin-login-view"));
document.getElementById("show-user-login").addEventListener("click", () => showView("user-login-view"));
document.getElementById("back-to-register").addEventListener("click", () => showView("request-view"));
document.getElementById("pending-login").addEventListener("click", () => showView("user-login-view"));
document.getElementById("back-to-request").addEventListener("click", () => showView("request-view"));
document.getElementById("fullscreen-player").addEventListener("click", async () => {
  const player = document.getElementById("video-player");
  const button = document.getElementById("fullscreen-player");
  try {
    if (document.fullscreenElement === player) {
      await document.exitFullscreen();
    } else {
      await player.requestFullscreen();
    }
  } catch (error) {
    showNotice("To‘liq ekran rejimini ochib bo‘lmadi. Qurilmangiz sozlamalarini tekshiring.", true);
  }
});
document.addEventListener("fullscreenchange", () => {
  const expanded = document.fullscreenElement === document.getElementById("video-player");
  const button = document.getElementById("fullscreen-player");
  button.setAttribute("aria-label", expanded ? "Kichraytirish" : "Kattalashtirish");
  button.title = expanded ? "Kichraytirish" : "Kattalashtirish";
});
document.getElementById("cancel-request").addEventListener("click", () => {
  localStorage.removeItem(requestKeyName);
  localStorage.removeItem(requestEmailName);
  showView("request-view");
});
document.querySelectorAll(".logout-button").forEach(button => {
  button.addEventListener("click", async () => {
    await api("/api/logout", { method: "POST", body: "{}" });
    if (userSessionTimer) clearInterval(userSessionTimer);
    userSessionTimer = null;
    showView("request-view");
  });
});

(async () => {
  if (await restoreSession()) return;
  const pendingKey = localStorage.getItem(requestKeyName);
  if (pendingKey) startStatusPolling(pendingKey);
})();
