"use client";
import BookingChat from "./BookingChat";

// Deliberately NOT wrapped in `AppLayout` — that component redirects anyone
// without a Supabase session straight to /login (see
// `components/layout/AppLayout.tsx`), which is exactly the visitor this page
// exists for. This route has its own minimal shell instead, and is not listed
// in `Sidebar.tsx`'s nav since it isn't part of the staff app.
export default function PublicBookingPage() {
  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      <header className="border-b border-gray-200 bg-white px-6 py-4">
        <h1 className="text-lg font-bold text-gray-900">Book an Appointment</h1>
        <p className="text-xs text-gray-500">Chat with us to find the right doctor and time.</p>
      </header>

      <main className="flex-1 px-4 pt-4">
        <BookingChat />
      </main>

      <footer className="border-t border-gray-200 bg-white px-6 py-3 text-center">
        <p className="text-xs text-gray-400">
          This assistant only handles scheduling — it cannot give medical advice.
          If this is a medical emergency, call <strong>112</strong> or go to your nearest emergency department.
        </p>
      </footer>
    </div>
  );
}
