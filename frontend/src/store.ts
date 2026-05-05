import { create } from "zustand";

interface AppState {
  authed: boolean;
  // `null` until the boot session probe finishes, then `true`/`false`.
  // Routes that depend on auth (PrivateOutlet, LoginPage redirect) must
  // wait for this to flip — otherwise the initial `false` value races
  // the async probe and you get bounced to /login on every refresh.
  bootChecked: boolean;
  email: string;
  selectedProjectId: string | null;
  selectedRunId: string | null;
  setAuthed: (v: boolean) => void;
  setBootChecked: (v: boolean) => void;
  setEmail: (e: string) => void;
  setSelectedProjectId: (id: string | null) => void;
  setSelectedRunId: (id: string | null) => void;
  banner: { message: string; tone: "info" | "success" | "error" } | null;
  flash: (message: string, tone?: "info" | "success" | "error") => void;
  clearFlash: () => void;
}

export const useApp = create<AppState>((set) => ({
  authed: false,
  bootChecked: false,
  email: "",
  selectedProjectId: null,
  selectedRunId: null,
  setAuthed: (v) => set({ authed: v }),
  setBootChecked: (v) => set({ bootChecked: v }),
  setEmail: (e) => set({ email: e }),
  setSelectedProjectId: (id) => set({ selectedProjectId: id }),
  setSelectedRunId: (id) => set({ selectedRunId: id }),
  banner: null,
  flash: (message, tone = "info") => {
    set({ banner: { message, tone } });
    window.setTimeout(() => set({ banner: null }), 5000);
  },
  clearFlash: () => set({ banner: null }),
}));
