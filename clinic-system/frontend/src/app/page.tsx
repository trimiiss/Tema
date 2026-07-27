"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

// A logged-in staff member has nothing to do on a landing page, so they skip
// straight to the dashboard. Everyone else sees a real choice instead of being
// forced into /login — this is also the public entry point for a visitor who
// just wants to book, not sign in.
export default function Root() {
  const router = useRouter();
  const [checkingSession, setCheckingSession] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (data.session) router.replace("/dashboard");
      else setCheckingSession(false);
    });
  }, [router]);

  if (checkingSession) return null;

  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      <main className="flex-1 flex flex-col items-center justify-center px-4">
        <div className="max-w-md w-full text-center space-y-8">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Clinic Admin System</h1>
            <p className="text-sm text-gray-500 mt-1">
              How would you like to continue?
            </p>
          </div>

          <div className="space-y-3">
            <button
              onClick={() => router.push("/book")}
              className="w-full px-5 py-4 bg-primary-600 hover:bg-primary-700 text-white font-semibold rounded-xl transition text-left"
            >
              <span className="block text-sm">Book an Appointment</span>
              <span className="block text-xs font-normal text-primary-100 mt-0.5">
                No account needed — chat with us to find a doctor and time.
              </span>
            </button>

            <button
              onClick={() => router.push("/login")}
              className="w-full px-5 py-4 bg-white border border-gray-200 hover:border-primary-400 hover:bg-primary-50 text-gray-800 font-semibold rounded-xl transition text-left"
            >
              <span className="block text-sm">Staff Login</span>
              <span className="block text-xs font-normal text-gray-500 mt-0.5">
                For doctors, receptionists and admins.
              </span>
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
