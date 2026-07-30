"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import {
  CalendarHeart,
  ArrowRight,
  Stethoscope,
  Baby,
  Activity,
  FileText,
  Clock,
  MapPin,
  ClipboardCheck,
} from "lucide-react";

const SPECIALTIES = [
  {
    icon: Stethoscope,
    title: "General Practice",
    description: "Everyday health concerns, checkups and referrals to the right specialist.",
  },
  {
    icon: Baby,
    title: "Pediatrics",
    description: "Care for children, from routine visits to follow-ups their parents can track.",
  },
  {
    icon: Activity,
    title: "Internal Medicine",
    description: "Ongoing management of adult and chronic conditions, visit to visit.",
  },
  {
    icon: FileText,
    title: "Document Handling",
    description: "Referrals and lab results scanned, filed and verified — nothing lost in a drawer.",
  },
];

const HIGHLIGHTS = [
  {
    icon: CalendarHeart,
    text: "Chat to find a doctor and a time — no phone tag, no waiting on hold.",
  },
  {
    icon: ClipboardCheck,
    text: "Confirmed the moment you accept a time — it's on the schedule instantly.",
  },
  {
    icon: FileText,
    text: "Documents and history kept in one place, verified and organized.",
  },
];

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
    <div className="min-h-screen bg-white">
      <header className="sticky top-0 z-10 bg-white/80 backdrop-blur border-b border-gray-100">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 bg-primary-600 rounded-lg flex items-center justify-center">
              <span className="text-white text-sm font-bold">T</span>
            </div>
            <span className="font-semibold text-gray-900">Trim&apos;s Clinic</span>
          </div>
          <button
            onClick={() => router.push("/login")}
            className="text-sm font-medium text-gray-500 hover:text-primary-600 transition-colors"
          >
            Staff Login
          </button>
        </div>
      </header>

      <main>
        {/* Hero */}
        <section className="max-w-5xl mx-auto px-4 sm:px-6 pt-16 pb-20 text-center">
          <div className="inline-flex items-center gap-1.5 text-xs font-medium text-primary-700 bg-primary-50 rounded-full px-3 py-1 mb-6">
            <MapPin className="w-3.5 h-3.5" />
            Mitrovica, Kosovo
          </div>
          <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900 max-w-2xl mx-auto">
            Care coordinated around you
          </h1>
          <p className="text-lg text-gray-500 mt-4 max-w-xl mx-auto">
            Trim&apos;s Clinic is a general and family-care clinic. Tell us what you need in plain
            language, and we&apos;ll find the right doctor and time — accept a slot and
            you&apos;re booked immediately, no callback needed.
          </p>
          <div className="flex items-center justify-center mt-8">
            <button
              onClick={() => router.push("/book")}
              className="group w-full sm:w-auto inline-flex items-center justify-center gap-2 px-6 py-3.5 bg-primary-600 hover:bg-primary-700 text-white font-semibold rounded-xl transition-all shadow-sm hover:shadow-md"
            >
              <CalendarHeart className="w-4.5 h-4.5" />
              Book an Appointment
              <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
            </button>
          </div>
        </section>

        {/* Who we are */}
        <section className="border-t border-gray-100 bg-gray-50/60">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 py-16 grid md:grid-cols-2 gap-10 items-center">
            <div>
              <h2 className="text-sm font-semibold text-primary-600 uppercase tracking-wide">
                Who we are
              </h2>
              <p className="text-2xl font-bold text-gray-900 mt-2 leading-snug">
                A clinic built around a quick, direct booking experience.
              </p>
              <p className="text-gray-500 mt-4 leading-relaxed">
                Tell us what you need in plain language and we&apos;ll match you with the right
                doctor and an open slot — accept it and it&apos;s on the schedule immediately, no
                waiting on a callback. Your documents and history stay organized in one place,
                checked by our staff, so nothing gets lost between visits.
              </p>
            </div>
            <div className="space-y-4">
              {HIGHLIGHTS.map(({ icon: Icon, text }) => (
                <div key={text} className="flex items-start gap-3 bg-white rounded-xl p-4 border border-gray-100 shadow-sm">
                  <div className="shrink-0 w-9 h-9 rounded-lg bg-primary-50 flex items-center justify-center">
                    <Icon className="w-4.5 h-4.5 text-primary-600" />
                  </div>
                  <p className="text-sm text-gray-600 leading-relaxed pt-1.5">{text}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* What we do */}
        <section className="max-w-5xl mx-auto px-4 sm:px-6 py-16">
          <div className="text-center max-w-lg mx-auto mb-10">
            <h2 className="text-sm font-semibold text-primary-600 uppercase tracking-wide">
              What we do
            </h2>
            <p className="text-2xl font-bold text-gray-900 mt-2">Care and admin, under one roof</p>
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {SPECIALTIES.map(({ icon: Icon, title, description }) => (
              <div
                key={title}
                className="p-5 rounded-2xl border border-gray-100 hover:border-primary-200 hover:shadow-md transition-all"
              >
                <div className="w-10 h-10 rounded-xl bg-primary-50 flex items-center justify-center mb-4">
                  <Icon className="w-5 h-5 text-primary-600" />
                </div>
                <h3 className="font-semibold text-gray-900 text-sm">{title}</h3>
                <p className="text-sm text-gray-500 mt-1.5 leading-relaxed">{description}</p>
              </div>
            ))}
          </div>
        </section>

        {/* CTA strip */}
        <section className="border-t border-gray-100">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 py-14 text-center">
            <p className="text-xl font-bold text-gray-900">Ready when you are</p>
            <p className="text-gray-500 mt-2">No account needed to request an appointment.</p>
            <button
              onClick={() => router.push("/book")}
              className="group inline-flex items-center justify-center gap-2 px-6 py-3.5 bg-primary-600 hover:bg-primary-700 text-white font-semibold rounded-xl transition-all shadow-sm hover:shadow-md mt-6"
            >
              <CalendarHeart className="w-4.5 h-4.5" />
              Book an Appointment
              <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-1" />
            </button>
          </div>
        </section>
      </main>

      <footer className="border-t border-gray-100">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-gray-400">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 bg-primary-600 rounded-md flex items-center justify-center">
              <span className="text-white text-xs font-bold">T</span>
            </div>
            <span className="font-medium text-gray-500">Trim&apos;s Clinic</span>
          </div>
          <div className="flex items-center gap-5">
            <span className="inline-flex items-center gap-1.5">
              <Clock className="w-3.5 h-3.5" />
              Mon–Fri · 09:00–17:00
            </span>
            <span className="inline-flex items-center gap-1.5">
              <MapPin className="w-3.5 h-3.5" />
              Mitrovica, Kosovo
            </span>
            <button onClick={() => router.push("/login")} className="hover:text-primary-600 transition-colors">
              Staff Login
            </button>
          </div>
        </div>
      </footer>
    </div>
  );
}
