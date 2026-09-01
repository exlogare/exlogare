/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_LANDING_URL?: string;
  readonly VITE_CONTACT_EMAIL?: string;
  readonly VITE_COMPANY_NAME?: string;
  readonly VITE_COMPANY_NAME_SHORT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
