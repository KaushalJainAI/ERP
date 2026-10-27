import axios from "axios";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

/** Fired when the session is irrecoverably unauthenticated; AuthProvider listens. */
export const AUTH_LOGOUT_EVENT = "auth:logout";

export const apiClient = axios.create({
  baseURL: API_URL,
  headers: {
    "Content-Type": "application/json",
  },
});

function clearSession() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  window.dispatchEvent(new Event(AUTH_LOGOUT_EVENT));
}

// Request Interceptor: Attach JWT Access Token & Language
apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem("access_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    const lang = localStorage.getItem("app_lang") || "en";
    config.headers["Accept-Language"] = lang;
    return config;
  },
  (error) => Promise.reject(error)
);

// Response Interceptor: Auto-refresh JWT on 401, then fall back to logout.
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    const isAuthCall = originalRequest?.url?.includes("/auth/token/");
    if (error.response?.status === 401 && originalRequest && !isAuthCall) {
      if (!originalRequest._retry) {
        originalRequest._retry = true;
        const refreshToken = localStorage.getItem("refresh_token");
        if (refreshToken) {
          try {
            const res = await axios.post(`${API_URL}/auth/token/refresh/`, {
              refresh: refreshToken,
            });
            const newAccessToken = res.data.access;
            localStorage.setItem("access_token", newAccessToken);
            if (res.data.refresh) localStorage.setItem("refresh_token", res.data.refresh);
            originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
            return apiClient(originalRequest);
          } catch {
            clearSession();
            return Promise.reject(error);
          }
        }
      }
      clearSession();
    }
    return Promise.reject(error);
  }
);
