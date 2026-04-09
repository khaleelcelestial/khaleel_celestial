/**
 * toast.js — Singleton Toast Notification System
 * Supports: success, error, warning, info
 * Features: auto-dismiss, progress bar, hover pause, max 5 visible, queue
 */

const ICONS = {
  success: "✅",
  error: "❌",
  warning: "⚠️",
  info: "ℹ️",
};

class Toast {
  constructor() {
    this.container = document.getElementById("toast-container");
    this.queue = [];
    this.active = [];
    this.MAX_VISIBLE = 5;
  }

  /**
   * Show a toast notification
   * @param {string} message
   * @param {'success'|'error'|'warning'|'info'} type
   * @param {number} duration ms, 0 = persistent
   */
  show(message, type = "info", duration = 4000) {
    const item = { message, type, duration };
    if (this.active.length >= this.MAX_VISIBLE) {
      this.queue.push(item);
      return;
    }
    this._render(item);
  }

  _render({ message, type, duration }) {
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");

    const icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.textContent = ICONS[type] || "📢";

    const body = document.createElement("div");
    body.className = "toast-body";

    const typeLabel = document.createElement("div");
    typeLabel.className = "toast-type";
    typeLabel.textContent = type.charAt(0).toUpperCase() + type.slice(1);

    const msg = document.createElement("div");
    msg.className = "toast-message";
    msg.textContent = message;

    body.appendChild(typeLabel);
    body.appendChild(msg);

    const closeBtn = document.createElement("button");
    closeBtn.className = "toast-close";
    closeBtn.innerHTML = "×";
    closeBtn.setAttribute("aria-label", "Close notification");
    closeBtn.addEventListener("click", () => this._dismiss(toast, progressBar, timerId));

    const progressBar = document.createElement("div");
    progressBar.className = "toast-progress";

    toast.appendChild(icon);
    toast.appendChild(body);
    toast.appendChild(closeBtn);
    toast.appendChild(progressBar);

    this.container.prepend(toast);
    this.active.push(toast);

    let timerId = null;
    let startTime = null;
    let remaining = duration;

    if (duration > 0) {
      progressBar.style.width = "100%";
      progressBar.style.transitionDuration = `${duration}ms`;

      // Start progress bar shrink after paint
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          progressBar.style.width = "0%";
        });
      });

      startTime = Date.now();
      timerId = setTimeout(() => this._dismiss(toast, progressBar, null), duration);

      // Pause on hover
      toast.addEventListener("mouseenter", () => {
        if (timerId) {
          clearTimeout(timerId);
          timerId = null;
          remaining -= Date.now() - startTime;
          progressBar.style.transitionDuration = "0ms";
          progressBar.style.width = `${(remaining / duration) * 100}%`;
        }
      });

      toast.addEventListener("mouseleave", () => {
        if (remaining > 0 && !timerId) {
          startTime = Date.now();
          requestAnimationFrame(() => {
            progressBar.style.transitionDuration = `${remaining}ms`;
            progressBar.style.width = "0%";
          });
          timerId = setTimeout(() => this._dismiss(toast, progressBar, null), remaining);
        }
      });
    }
  }

  _dismiss(toast, progressBar, timerId) {
    if (timerId) clearTimeout(timerId);
    toast.classList.add("removing");
    toast.addEventListener("animationend", () => {
      toast.remove();
      this.active = this.active.filter((t) => t !== toast);
      if (this.queue.length > 0) {
        this._render(this.queue.shift());
      }
    }, { once: true });
  }

  static getInstance() {
    if (!Toast._instance) Toast._instance = new Toast();
    return Toast._instance;
  }
}

export const toast = Toast.getInstance();
