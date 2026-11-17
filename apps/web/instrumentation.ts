import * as Sentry from "@sentry/nextjs";
import { sentryPrivate } from "@/lib/sentry-private";


// Sentry stays off (no-op) until NEXT_PUBLIC_SENTRY_DSN is set.
export function register() {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT ?? "development",
    dataCollection: sentryPrivate,
    tracesSampleRate: 0,
  });
}

export const onRequestError = Sentry.captureRequestError;
