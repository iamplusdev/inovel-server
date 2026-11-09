import { http } from "./client";
import type {
  AdminStats,
  BookDetail,
  BookListResponse,
  ReadingStats,
} from "./types";

/** 管理端书库 / 统计 API */
export function fetchStats() {
  return http.get<AdminStats>("/api/admin/stats");
}

export interface BookQuery {
  page?: number;
  page_size?: number;
  sort?: string;
  q?: string;
  source?: string;
  category?: string;
  tag?: string;
  status?: string;
  /** unscraped=仅未刮削 */
  scraped?: string;
  /** true=仅读过的书（配合 sort=read 做继续阅读） */
  read?: boolean;
  /** 可选字段裁剪，如 id,name,author,cover_thumb_url */
  fields?: string;
}

export function fetchBooks(query: BookQuery) {
  // 展开为可序列化 query 对象，避免 TS 索引签名限制
  return http.get<BookListResponse>("/api/admin/books", { ...query });
}

export function fetchBook(id: number | string) {
  return http.get<BookDetail>(`/api/admin/books/${id}`);
}

/** 阅读统计概览 */
export function fetchReadingStats() {
  return http.get<ReadingStats>("/api/admin/stats/reading");
}

export function updateBook(id: number | string, body: Record<string, unknown>) {
  return http.patch<BookDetail>(`/api/admin/books/${id}`, body);
}

export function deleteBook(id: number | string) {
  return http.delete<{ ok?: boolean }>(`/api/admin/books/${id}`);
}

/** 批量删除（书库多选） */
export function batchDeleteBooks(ids: number[]) {
  return http.post<{ ok: boolean; deleted_count: number; deleted: string[]; ids: number[] }>(
    "/api/admin/books/batch/delete",
    { ids },
  );
}

/** 批量编辑分类 / 标签 / 状态 */
export interface BatchUpdateBody {
  ids: number[];
  category_source?: string;
  category_name?: string;
  tags?: string;
  tags_mode?: "append" | "remove" | "replace";
  status?: string;
}

export function batchUpdateBooks(body: BatchUpdateBody) {
  return http.post<{ ok: boolean; updated_count: number; ids: number[] }>(
    "/api/admin/books/batch/update",
    body,
  );
}

/** 写入阅读进度（百分比 0–100） */
export function updateReadProgress(
  id: number | string,
  body: { percent: number; chapter_index?: number },
) {
  return http.put<{
    ok: boolean;
    id: number;
    read_percent: number;
    read_chapter_index: number;
    read_at: string;
  }>(`/api/admin/books/${id}/progress`, body);
}

export function uploadCover(id: number | string, file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return http.upload<{ ok?: boolean; cover_url?: string }>(`/api/admin/books/${id}/cover`, fd);
}

/** 阅读器：完整目录（分页，page_size 默认 500） */
export function fetchBookChapters(bookId: number | string, page = 1, pageSize = 500) {
  return http.get<{
    book_id: number;
    book_name: string;
    total: number;
    page: number;
    page_size: number;
    items: { id: number; index: number; name: string; title: string }[];
  }>(`/api/books/${bookId}/chapters`, { page, page_size: pageSize });
}

export interface TocChapter {
  id: number;
  index: number;
  title: string;
}

/** 目录展示序号：库内 index 为 0 基，界面统一用 1 基 */
export function tocNo(index: number): number {
  return (Number(index) || 0) + 1;
}

/** 目录标题：空标题或与书名重复时返回空（由序号列展示），避免出现「0 书名」伪条目 */
export function tocTitle(
  title: string | undefined | null,
  bookName?: string | null,
): string {
  const t = (title || "").trim();
  const name = (bookName || "").trim();
  if (!t || (name && t === name)) return "";
  return t;
}

/** 目录伪占位条目（空标题/书名重复）：列表中直接过滤，不展示 */
export function isTocPlaceholder(
  title: string | undefined | null,
  bookName?: string | null,
): boolean {
  return tocTitle(title, bookName) === "";
}

/** 阅读页章名：无有效标题时回退「第 N 章」 */
export function chapterHeading(
  title: string | undefined | null,
  index: number,
  bookName?: string | null,
): string {
  return tocTitle(title, bookName) || `第 ${tocNo(index)} 章`;
}

/** 拉取全部目录（按 total 翻页），返回列表 + 真实总章数 */
export async function fetchAllBookChapters(bookId: number | string): Promise<{
  items: TocChapter[];
  total: number;
}> {
  const pageSize = 500;
  const first = await fetchBookChapters(bookId, 1, pageSize);
  const items: TocChapter[] = (first.items || []).map((c) => ({
    id: c.id,
    index: c.index,
    title: c.title || c.name || "",
  }));
  const total = Number(first.total) || items.length;
  let page = 2;
  while (items.length < total) {
    const next = await fetchBookChapters(bookId, page, pageSize);
    const chunk = next.items || [];
    if (!chunk.length) break;
    for (const c of chunk) {
      items.push({ id: c.id, index: c.index, title: c.title || c.name || "" });
    }
    page += 1;
  }
  return { items, total };
}

/** 书内搜索命中项 */
export interface BookSearchHit {
  chapter_id: number;
  index: number;
  title: string;
  /** 章内字符偏移，用于跳转到匹配位置 */
  offset: number;
  snippet: string;
}

/** 书内搜索：扫描本书正文，返回章节 + 上下文片段 */
export function searchBookContent(bookId: number | string, q: string, limit = 100) {
  return http.get<{ q: string; total: number; items: BookSearchHit[] }>(
    `/api/admin/books/${bookId}/search`,
    { q, limit },
  );
}

/** 阅读器：章节正文 */
export function fetchChapterContent(bookId: number | string, chapterId: number | string) {
  return http.get<{
    id: number;
    book_id: number;
    index: number;
    title: string;
    content: string;
  }>(`/api/books/${bookId}/chapters/${chapterId}`);
}

/** WebDAV 进度同步配置（密码只写不读） */
export interface WebDavSettings {
  enabled: boolean;
  url: string;
  username: string;
  dir: string;
  timeout: number;
  verify_tls: boolean;
  has_password: boolean;
}

export interface WebDavSettingsBody {
  enabled?: boolean;
  url?: string;
  username?: string;
  password?: string;
  dir?: string;
  timeout?: number;
  verify_tls?: boolean;
  clear_password?: boolean;
}

export function fetchWebDavSettings() {
  return http.get<WebDavSettings>("/api/admin/settings/webdav");
}

export function saveWebDavSettings(body: WebDavSettingsBody) {
  return http.put<{ ok: boolean } & WebDavSettings>("/api/admin/settings/webdav", body);
}

export function testWebDav() {
  return http.post<{ ok: boolean; message?: string; error?: string; root?: string }>(
    "/api/admin/settings/webdav/test",
  );
}

/** 打开阅读页时拉取 Legado 远端进度 */
export interface PullProgressResult {
  enabled: boolean;
  found?: boolean;
  updated: boolean;
  chapter_id?: number;
  chapter_index?: number;
  chapter_title?: string;
  chapter_pos?: number;
  remote_time?: number;
  filename?: string;
  error?: string;
  reason?: string;
}

export function pullBookProgress(bookId: number | string) {
  return http.post<PullProgressResult>(`/api/admin/books/${bookId}/sync/pull`);
}

/** 书库打开时批量拉取 WebDAV 进度（只同步书库中已存在的书籍） */
export interface PullAllProgressResult {
  enabled: boolean;
  listed: boolean;
  /** 远端 bookProgress 下的进度文件总数 */
  remote_files: number;
  /** 命中书库的书籍数 */
  matched: number;
  /** 书库外、被忽略的进度文件数（手机书架往往比服务端书库大） */
  ignored: number;
  /** 被忽略文件名样例，便于排查「某本书为何没同步」 */
  ignored_sample: string[];
  scanned: number;
  updated: number;
  errors: number;
  books: Array<{
    book_id: number;
    chapter_id?: number;
    chapter_index?: number;
    remote_time?: number;
  }>;
}

export function pullAllBookProgress() {
  return http.post<PullAllProgressResult>("/api/admin/sync/pull-all");
}

/** 详情页同步诊断 */
export interface SyncInfo {
  enabled: boolean;
  configured: boolean;
  filename: string;
  url: string;
  local: {
    read_chapter_index: number;
    read_percent: number;
    read_at: string;
  };
  remote_exists: boolean;
  remote: {
    name: string;
    author: string;
    durChapterIndex: number;
    durChapterPos: number;
    durChapterTime: number;
    durChapterTitle: string;
  } | null;
  matched: { chapter_id: number; index: number; title: string } | null;
  error: string;
}

export function fetchSyncInfo(bookId: number | string) {
  return http.get<SyncInfo>(`/api/admin/books/${bookId}/sync/info`);
}

/** 手动推送本地当前进度 */
export function pushBookProgressNow(bookId: number | string) {
  return http.post<{
    enabled: boolean;
    pushed: boolean;
    skipped?: string;
    filename?: string;
    remote_time?: number;
    error?: string;
  }>(`/api/admin/books/${bookId}/sync/push-now`);
}

/** 自动体检设置（服务端调度器读取） */
export interface MaintenanceSettings {
  scan_enabled: boolean;
  scan_interval_hours: number;
  scan_deep: boolean;
  last_scan_at: string;
  last_scan_error: string;
}

export function fetchMaintenanceSettings() {
  return http.get<MaintenanceSettings>("/api/admin/settings/maintenance");
}

export function saveMaintenanceSettings(payload: {
  scan_enabled?: boolean;
  scan_interval_hours?: number;
  scan_deep?: boolean;
}) {
  return http.put<{ ok: boolean } & MaintenanceSettings>(
    "/api/admin/settings/maintenance",
    payload,
  );
}

/** 刮削参数（设置页可调） */
export interface ScrapeSettingSpec {
  key: string;
  type: "float" | "int" | "bool";
  min?: number;
  max?: number;
  label: string;
  unit?: string;
  hint?: string;
}

export type ScrapeSettingsValues = Record<string, number | boolean>;

export function fetchScrapeSettings() {
  return http.get<{ values: ScrapeSettingsValues; spec: ScrapeSettingSpec[] }>(
    "/api/admin/settings/scrape",
  );
}

export function saveScrapeSettings(values: ScrapeSettingsValues) {
  return http.put<{ ok: boolean; values: ScrapeSettingsValues }>(
    "/api/admin/settings/scrape",
    values,
  );
}

/** 批量回填封面缩略图 */
export interface ThumbRebuildResult {
  ok: boolean;
  available: boolean;
  total: number;
  generated: number;
  skipped: number;
  failed: number;
  width: number;
  force: boolean;
}

export function rebuildThumbs(payload: {
  book_ids?: number[];
  limit?: number;
  force?: boolean;
  width?: number;
} = {}) {
  return http.post<ThumbRebuildResult>("/api/admin/books/thumbs/rebuild", payload);
}

// —— Legado 书源管理 ——

export interface BookSourceItem {
  id: number;
  source_url: string;
  name: string;
  group_name: string;
  source_type: number;
  enabled: boolean;
  enabled_explore: boolean;
  weight: number;
  remark: string;
  last_checked_at: string;
  last_check_ok: boolean;
  created_at: string;
  updated_at: string;
  /** 仅详情返回：完整 Legado 书源对象 */
  raw?: Record<string, unknown>;
}

export interface BookSourceList {
  items: BookSourceItem[];
  total: number;
  page: number;
  page_size: number;
  groups: string[];
}

export interface BookSourceSettings {
  subscribe_token: string;
  public_base_url: string;
  subscribe_path: string;
  subscribe_url: string;
  token_created_at: string;
}

export interface BookSourceImportResult {
  total: number;
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
}

export interface BookSourceCheck {
  label: string;
  ok: boolean;
  level: "ok" | "error" | "warning";
  detail: string;
}

export interface BookSourceProbe {
  ok: boolean;
  status: number;
  ms: number;
  final_url: string;
  error: string;
  skipped: string;
}

export interface BookSourceValidation {
  ok: boolean;
  errors: string[];
  warnings: string[];
  checks: BookSourceCheck[];
  probe?: BookSourceProbe | null;
  id?: number;
  name?: string;
}

export interface BookSourceQuery {
  [key: string]: string | number | boolean | undefined;
  q?: string;
  group?: string;
  enabled?: boolean;
  page?: number;
  page_size?: number;
}

export function fetchBookSources(query: BookSourceQuery = {}) {
  return http.get<BookSourceList>("/api/admin/book-sources", query);
}

export function fetchBookSource(id: number) {
  return http.get<BookSourceItem>(`/api/admin/book-sources/${id}`);
}

export function createBookSource(payload: {
  source?: Record<string, unknown>;
  sources?: Record<string, unknown>[];
  overwrite?: boolean;
  remark?: string;
}) {
  return http.post<BookSourceImportResult>("/api/admin/book-sources", payload);
}

export function updateBookSource(
  id: number,
  payload: { source: Record<string, unknown>; remark?: string },
) {
  return http.put<BookSourceItem>(`/api/admin/book-sources/${id}`, payload);
}

export function patchBookSource(
  id: number,
  payload: Partial<Pick<BookSourceItem, "enabled" | "enabled_explore" | "group_name" | "weight" | "remark">>,
) {
  return http.patch<BookSourceItem>(`/api/admin/book-sources/${id}`, payload);
}

export function deleteBookSource(id: number) {
  return http.delete<{ deleted: number }>(`/api/admin/book-sources/${id}`);
}

export function importBookSources(payload: {
  text?: string;
  url?: string;
  sources?: Record<string, unknown>[];
  overwrite?: boolean;
}) {
  return http.post<BookSourceImportResult>("/api/admin/book-sources/import", payload);
}

export function batchBookSources(payload: {
  ids: number[];
  action: "enable" | "disable" | "delete" | "group";
  group?: string;
}) {
  return http.post<{ affected: number }>("/api/admin/book-sources/batch", payload);
}

export function validateBookSource(payload: {
  source?: Record<string, unknown>;
  ids?: number[];
  probe?: boolean;
  exclude_id?: number;
}) {
  return http.post<BookSourceValidation | { items: BookSourceValidation[] }>(
    "/api/admin/book-sources/validate",
    payload,
  );
}

export function validateStoredBookSource(id: number, probe: boolean) {
  return http.post<BookSourceValidation>(`/api/admin/book-sources/${id}/validate`, { probe });
}

export function fetchBookSourceSettings() {
  return http.get<BookSourceSettings>("/api/admin/book-sources/settings");
}

export function resetBookSourceToken() {
  return http.post<BookSourceSettings>("/api/admin/book-sources/settings/reset-token");
}

/** 导出下载链接（浏览器带会话 Cookie，直接 window.open / fetch 即可） */
export function exportBookSourcesUrl(params: { ids?: number[]; group?: string; enabled_only?: boolean } = {}) {
  const sp = new URLSearchParams();
  if (params.ids?.length) sp.set("ids", params.ids.join(","));
  if (params.group) sp.set("group", params.group);
  if (params.enabled_only) sp.set("enabled_only", "true");
  const qs = sp.toString();
  return `/api/admin/book-sources/export${qs ? `?${qs}` : ""}`;
}
