/**
 * Centralized UI string table.
 *
 * This is a deliberately thin layer — not a full i18n framework. It gives
 * every Korean UI string a single home so:
 *   - search-and-replace for copy tweaks touches one file
 *   - future locale switching has a concrete place to plug in (e.g. swap
 *     `STRINGS` for a `useLocale()` hook that returns the same shape)
 *   - duplicate phrases ("재연결 중…", "관리자 권한이 필요합니다") are
 *     defined once instead of drifting across components
 *
 * Convention: if a string is used in more than one file, it MUST live here.
 * Single-use component copy may stay inline for readability, but new
 * strings are welcome here as well.
 */

export const STRINGS = {
  common: {
    reconnecting: "재연결 중…",
    close: "닫기",
    loading: "불러오는 중...",
    refresh: "새로고침 ⟳",
    refreshing: "새로고침 중...",
    noData: "데이터 없음",
  },
  admin: {
    onlyAdmin: "관리자만 접근할 수 있습니다.",
    adminRequired: "관리자 권한이 필요합니다.",
  },
  auth: {
    invalidCredentials: "사용자 이름 또는 비밀번호가 올바르지 않습니다.",
    notActivated:
      "계정이 아직 활성화되지 않았습니다. 관리자의 승인을 기다려 주세요.",
    usernameTaken: "이미 사용 중인 사용자 이름입니다.",
    invalidInput: "입력값이 올바르지 않습니다. 규칙을 확인해 주세요.",
    networkError:
      "네트워크 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
  },
  model: {
    settingsTitle: "모델 설정",
  },
} as const;
