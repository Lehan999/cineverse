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
let savedTheme = null;
try { savedTheme = window.localStorage.getItem("cineverse-theme"); } catch (_) { /* Theme storage is optional. */ }
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
    try { window.localStorage.setItem("cineverse-theme", nextTheme); } catch (_) { /* Keep the current page usable when storage is blocked. */ }
    updateThemeButton();
});

const heroCarousel = document.querySelector("[data-hero-carousel]");
if (heroCarousel) {
    const slides = Array.from(heroCarousel.querySelectorAll("[data-hero-slide]"));
    const dots = Array.from(heroCarousel.querySelectorAll("[data-carousel-dot]"));
    const previousButton = heroCarousel.querySelector("[data-carousel-previous]");
    const nextButton = heroCarousel.querySelector("[data-carousel-next]");
    const intervalMs = Math.max(3000, Number(heroCarousel.dataset.interval) || 6000);
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let activeIndex = Math.max(0, slides.findIndex((slide) => slide.classList.contains("is-active")));
    let timer = null;

    function showSlide(nextIndex, direction = 1) {
        const targetIndex = (nextIndex + slides.length) % slides.length;
        if (targetIndex === activeIndex) return;

        const oldSlide = slides[activeIndex];
        oldSlide.classList.remove("is-active", "is-before", "is-after");
        oldSlide.classList.add(direction > 0 ? "is-before" : "is-after");
        oldSlide.setAttribute("aria-hidden", "true");
        oldSlide.inert = true;

        const newSlide = slides[targetIndex];
        newSlide.classList.remove("is-before", "is-after");
        newSlide.classList.add("is-active");
        newSlide.setAttribute("aria-hidden", "false");
        newSlide.inert = false;

        dots.forEach((dot, index) => {
            const isActive = index === targetIndex;
            dot.classList.toggle("is-active", isActive);
            dot.setAttribute("aria-pressed", String(isActive));
        });
        activeIndex = targetIndex;
    }

    function stopAutoplay() {
        if (timer !== null) {
            window.clearInterval(timer);
            timer = null;
        }
    }

    function startAutoplay() {
        stopAutoplay();
        if (!reduceMotion.matches && !document.hidden && slides.length > 1) {
            timer = window.setInterval(() => showSlide(activeIndex + 1, 1), intervalMs);
        }
    }

    nextButton?.addEventListener("click", () => {
        showSlide(activeIndex + 1, 1);
        startAutoplay();
    });
    previousButton?.addEventListener("click", () => {
        showSlide(activeIndex - 1, -1);
        startAutoplay();
    });
    dots.forEach((dot) => {
        dot.addEventListener("click", () => {
            const targetIndex = Number(dot.dataset.carouselDot);
            showSlide(targetIndex, targetIndex > activeIndex ? 1 : -1);
            startAutoplay();
        });
    });

    heroCarousel.addEventListener("mouseenter", stopAutoplay);
    heroCarousel.addEventListener("mouseleave", startAutoplay);
    heroCarousel.addEventListener("focusin", stopAutoplay);
    heroCarousel.addEventListener("focusout", (event) => {
        if (!heroCarousel.contains(event.relatedTarget)) startAutoplay();
    });
    document.addEventListener("visibilitychange", startAutoplay);
    reduceMotion.addEventListener?.("change", startAutoplay);
    startAutoplay();
}
