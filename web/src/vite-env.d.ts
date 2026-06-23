/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_LANDING_URL?: string;
  readonly VITE_CONTACT_EMAIL?: string;
  readonly VITE_COMPANY_NAME?: string;
  readonly VITE_COMPANY_NAME_SHORT?: string;
  readonly VITE_COMPANY_INN?: string;
  readonly VITE_COMPANY_OGRNIP?: string;
  readonly VITE_COMPANY_PHONE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
