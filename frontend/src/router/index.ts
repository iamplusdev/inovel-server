import { createRouter, createWebHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";
import { useAuthStore } from "@/stores/auth";

/** 路由表：与旧 UI 视图一一对应，具体页面按阶段逐步落地 */
const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/LoginView.vue"),
    meta: { public: true },
  },
  {
    path: "/",
    component: () => import("@/layouts/AdminLayout.vue"),
    children: [
      {
        path: "",
        name: "library",
        component: () => import("@/views/LibraryView.vue"),
        meta: { title: "书库" },
      },
      {
        path: "online-search",
        name: "online-search",
        component: () => import("@/views/OnlineSearchView.vue"),
        meta: { title: "在线搜索" },
      },
      {
        path: "import",
        name: "import",
        component: () => import("@/views/ImportView.vue"),
        meta: { title: "导入" },
      },
      {
        path: "maintenance",
        name: "maintenance",
        component: () => import("@/views/MaintenanceView.vue"),
        meta: { title: "维护" },
        children: [
          { path: "", redirect: { name: "check" } },
          {
            path: "check",
            name: "check",
            component: () => import("@/views/CheckView.vue"),
            meta: { title: "书库体检" },
          },
          {
            path: "shelf",
            name: "shelf-restore",
            component: () => import("@/views/ShelfRestoreView.vue"),
            meta: { title: "书架备份恢复" },
          },
        ],
      },
      {
        path: "stats",
        name: "stats",
        component: () => import("@/views/StatsView.vue"),
        meta: { title: "阅读统计" },
      },
      {
        path: "access",
        name: "access",
        component: () => import("@/views/AccessView.vue"),
        meta: { title: "接入" },
        children: [
          { path: "", redirect: { name: "book-sources" } },
          {
            path: "sources",
            name: "book-sources",
            component: () => import("@/views/BookSourcesView.vue"),
            meta: { title: "书源管理" },
          },
          {
            path: "api",
            name: "api-docs",
            component: () => import("@/views/ApiView.vue"),
            meta: { title: "API 与书源 JSON" },
          },
        ],
      },
      // 旧路径兼容：保留 query（如 /check?tab=scrape）
      { path: "check", redirect: (to) => ({ name: "check", query: to.query }) },
      { path: "shelf-restore", redirect: (to) => ({ name: "shelf-restore", query: to.query }) },
      { path: "api-docs", redirect: (to) => ({ name: "api-docs", query: to.query }) },
      { path: "book-sources", redirect: (to) => ({ name: "book-sources", query: to.query }) },
      {
        path: "settings",
        name: "settings",
        component: () => import("@/views/SettingsView.vue"),
        meta: { title: "设置" },
      },
      {
        path: "books/:id",
        name: "book-detail",
        component: () => import("@/views/BookDetailView.vue"),
        meta: { title: "书籍详情" },
      },
    ],
  },
  // 阅读页独立全屏：不嵌 AdminLayout，无管理侧栏
  {
    path: "/books/:id/read",
    name: "book-read",
    component: () => import("@/views/ReaderView.vue"),
    meta: { title: "阅读" },
  },
  // SPA fallback：未知路径回书库
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

/** 会话守卫：未登录进 login；已登录访问 login 回书库 */
router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!auth.checked) {
    try {
      await auth.tryRestore();
    } catch {
      /* 网络异常时放行到登录页展示错误 */
    }
  }
  if (to.meta.public) {
    if (auth.isAuthed && to.name === "login") return { name: "library" };
    return true;
  }
  if (!auth.isAuthed) return { name: "login" };
  return true;
});

export default router;
