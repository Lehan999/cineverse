const loadingMessage = document.querySelector("#page-loading");

function showLoading(message, form = null) {
    if (loadingMessage) {
        loadingMessage.textContent = message;
        loadingMessage.hidden = false;
    }

    const submitButton = form?.querySelector("button[type='submit'], input[type='submit']");
    if (submitButton) {
        submitButton.disabled = true;
        submitButton.setAttribute("aria-busy", "true");
    }
}

document.querySelectorAll(".search-form").forEach((form) => {
    form.addEventListener("submit", () => showLoading("Searching movies…", form));
});

document.querySelectorAll(".movie-link").forEach((link) => {
    link.addEventListener("click", () => showLoading("Loading movie details…"));
});

document.querySelectorAll(".review-form").forEach((form) => {
    form.addEventListener("submit", () => showLoading("Saving your rating and review…", form));
});

const themeToggle = document.querySelector("#theme-toggle");
const savedTheme = localStorage.getItem("cineverse-theme");
document.documentElement.dataset.theme = savedTheme === "light" ? "light" : "dark";

function updateThemeButton() {
    const isDark = document.documentElement.dataset.theme !== "light";
    if (!themeToggle) return;
    themeToggle.setAttribute("aria-pressed", String(isDark));
    themeToggle.setAttribute("aria-label", "Switch to " + (isDark ? "light" : "dark") + " theme");
    themeToggle.innerHTML = (isDark ? "☼" : "☾") + " <span>" + (isDark ? "Light" : "Dark") + "</span>";
}

updateThemeButton();
themeToggle?.addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = nextTheme;
    localStorage.setItem("cineverse-theme", nextTheme);
    updateThemeButton();
});
