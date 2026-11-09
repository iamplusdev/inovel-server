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
