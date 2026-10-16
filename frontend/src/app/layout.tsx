import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Signal — Feature Intelligence",
  description: "Turn customer feedback into product clarity.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {/* Stated plainly rather than hidden: there is no authentication here. */}
        <p className="bg-surface-muted px-4 py-1.5 text-center text-xs text-muted">
          Demo environment — no authentication. Not production-ready.
        </p>

        <header className="border-b border-border-subtle bg-surface">
          <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
            <Link href="/" className="flex flex-col">
              <span className="text-base font-semibold tracking-tight text-foreground">
                Signal
              </span>
              <span className="text-xs text-muted">
                Turn customer feedback into product clarity
              </span>
            </Link>
            <nav aria-label="Main">
              <Link
                href="/"
                className="rounded-lg px-3 py-2 text-sm font-medium text-muted transition-colors hover:bg-surface-muted hover:text-foreground"
              >
                Request board
              </Link>
            </nav>
          </div>
        </header>

        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6 sm:py-10">
          {children}
        </main>

        <footer className="border-t border-border-subtle px-4 py-6 text-center text-xs text-subtle sm:px-6">
          Signal · AI recommends, humans decide
        </footer>
      </body>
    </html>
  );
}
