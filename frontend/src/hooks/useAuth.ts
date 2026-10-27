// Auth state lives in a single context (src/context/AuthContext.tsx) so every
// caller shares one user/session. Re-exported here for existing imports.
export { useAuth } from "@/context/AuthContext";
export type { UserProfile } from "@/context/AuthContext";
