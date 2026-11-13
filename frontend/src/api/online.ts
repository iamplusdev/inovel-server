/**
 * 在线搜索 API：用已导入的 Legado 书源在服务端检索，可登记进书库。
 */
import { http } from "./client";

export interface OnlineSourceRef {
  source_id: number;
  source_name: string;
  source_url: string;
  book_url: string;
  toc_url: string;
  cover_url: string;
  intro: string;
  kind: string;
  last_chapter: string;
}

/** 一条搜索结果（已按「书名+作者」聚合，sources 是同书的不同书源） */
export interface OnlineItem {
  key: string;
  name: string;
  author: string;
  cover_url: string;
  intro: string;
  kind: string;
  last_chapter: string;
  word_count: number;
  sources: OnlineSourceRef[];
}

export interface OnlineSourceStatus {
  id: number;
  name: string;
  count: number;
  error: string;
}

export interface OnlineSearchResult {
  q: string;
  items: OnlineItem[];
  sources: OnlineSourceStatus[];
  total: number;
  failed?: number;
  hint?: string;
}

/** 可作为搜索范围的书源 */
export interface OnlineSourceOption {
  id: number;
  name: string;
  group: string;
  source_url: string;
  has_rules: boolean;
  last_check_ok: boolean;
}

export interface OnlineAddBody {
  source_id: number;
  name: string;
  author?: string;
  book_url: string;
  toc_url?: string;
  cover_url?: string;
  intro?: string;
  kind?: string;
  last_chapter?: string;
  word_count?: number | string;
}

export interface OnlineAddResult {
  book_id: number;
  created: boolean;
  title: string;
  hint: string;
}

export function fetchOnlineSources() {
  return http.get<{ items: OnlineSourceOption[]; total: number }>("/api/admin/online/sources");
}

export function searchOnline(payload: {
  q: string;
  source_ids?: number[];
  page?: number;
  limit?: number;
}) {
  return http.post<OnlineSearchResult>("/api/admin/online/search", payload);
}

export function addOnlineBook(payload: OnlineAddBody) {
  return http.post<OnlineAddResult>("/api/admin/online/add", payload);
}

/** 同步在线书目录（加入书库后、打开阅读器前调用；force 用于刷新） */
export function syncOnlineToc(bookId: number | string, force = false) {
  return http.post<{ ok: boolean; book_id: number; synced: boolean; total: number }>(
    `/api/admin/online/books/${bookId}/toc`,
    { force },
  );
}

/** 换源候选：同一本书在另一个书源上的登记信息 */
export interface OnlineSourceCandidate {
  source_id: number;
  source_name: string;
  book_url: string;
  toc_url: string;
  last_chapter: string;
  kind: string;
  word_count: number;
  /** 是否就是当前正在使用的书源 */
  current: boolean;
}

export interface OnlineBookSourcesResult {
  ok: boolean;
  book_id: number;
  current: { source_id: number; source_name: string };
  items: OnlineSourceCandidate[];
  total: number;
  failed?: number;
  hint?: string;
}

/** 按书名/作者搜索其他书源（阅读器「切换书源」列表） */
export function listOnlineBookSources(bookId: number | string) {
  return http.post<OnlineBookSourcesResult>(
    `/api/admin/online/books/${bookId}/sources`,
    {},
  );
}

export interface OnlineSwitchSourceBody {
  source_id: number;
  book_url: string;
  toc_url?: string;
  word_count?: number;
}

/** 把在线书切到另一个书源；服务端会重建目录并清理旧正文缓存 */
export function switchOnlineBookSource(bookId: number | string, body: OnlineSwitchSourceBody) {
  return http.post<{
    ok: boolean;
    book_id: number;
    source_id: number;
    source_name: string;
    total: number;
  }>(`/api/admin/online/books/${bookId}/switch-source`, body);
}
