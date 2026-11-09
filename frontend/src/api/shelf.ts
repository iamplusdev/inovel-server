/** WebDAV 备份恢复 / 书架对照 API。 */
import { http } from "./client";
import type {
  BookListResponse,
  ShelfBackupFile,
  ShelfCompare,
  ShelfPreview,
  ShelfRestoreResult,
  ShelfStatus,
} from "./types";

export type { ShelfBackupFile, ShelfCompare, ShelfStatus } from "./types";

export interface ShelfBackupList {
  configured: boolean;
  items: ShelfBackupFile[];
  error: string;
}

export interface ShelfInput {
  /** WebDAV 上的备份文件路径（来自 /backups） */
  files?: string[];
  /** 本地上传：base64 内容 + 文件名 */
  contents?: Array<{ name: string; content: string }>;
}

export interface ShelfRestorePayload extends ShelfInput {
  restore_books?: boolean;
  restore_sources?: boolean;
  overwrite_sources?: boolean;
  replace_shelf?: boolean;
}

export function fetchShelfStatus() {
  return http.get<ShelfStatus>("/api/admin/shelf/status");
}

export function fetchShelfBackups() {
  return http.get<ShelfBackupList>("/api/admin/shelf/backups");
}

export function previewShelfBackup(payload: ShelfInput) {
  return http.post<ShelfPreview>("/api/admin/shelf/preview", payload);
}

export function restoreShelfBackup(payload: ShelfRestorePayload) {
  return http.post<ShelfRestoreResult>("/api/admin/shelf/restore", payload);
}

export function fetchShelfCompare(limit = 200) {
  return http.get<ShelfCompare>("/api/admin/shelf/compare", { limit });
}

export function clearShelf() {
  return http.post<{ deleted: number }>("/api/admin/shelf/clear", {});
}

export function deleteShelfBook(shelfId: number) {
  return http.delete<{ deleted: number }>(`/api/admin/shelf/books/${shelfId}`);
}

export function fetchShelfBook(shelfId: number) {
  return http.get<Record<string, unknown>>(`/api/admin/shelf/books/${shelfId}`);
}

/** 合并书架分页（本地书 + 仅 Legado 在线书） */
export function fetchMergedBooks(query: Record<string, string | number | boolean | undefined>) {
  return http.get<BookListResponse>("/api/admin/shelf/books", query);
}
