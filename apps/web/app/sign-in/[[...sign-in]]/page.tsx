import { SignIn } from "@clerk/nextjs";
import { Suspense } from "react";

export default function Page() {
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <Suspense>
        <SignIn />
      </Suspense>
    </main>
  );
}
