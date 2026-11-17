import * as Sentry from "@sentry/nextjs";
import { sentryPrivate } from "@/lib/sentry-private";

// Errors only: no tracing, no session replay. Off until a DSN is set.
const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
if (dsn) {
  Sentry.init({ dsn, dataCollection: sentryPrivate, tracesSampleRate: 0 });
}
