import type { BrowserOptions } from "@sentry/nextjs";

// Sentry 11 replaced sendDefaultPii: collect no user info, cookies, headers, bodies or query params.
export const sentryPrivate: NonNullable<BrowserOptions["dataCollection"]> = {
  userInfo: false,
  cookies: false,
  httpHeaders: false,
  httpBodies: [],
  urlQueryParams: false,
};
