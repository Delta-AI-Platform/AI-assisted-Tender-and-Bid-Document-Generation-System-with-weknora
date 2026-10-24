import { createI18n } from 'vue-i18n'
import zhCN from './locales/zh-CN.ts'
import ruRU from './locales/ru-RU.ts'
import enUS from './locales/en-US.ts'
import koKR from './locales/ko-KR.ts'
import jaJP from './locales/ja-JP.ts'
import zhTW from './locales/zh-TW.ts'
import { BUILT_IN_DEFAULT, resolveDefaultLocale } from './resolveDefaultLocale.ts'
import { pluralRules } from './pluralizationRules.ts'

// Deployment branding applies to display strings only; translation keys and URLs stay stable.
function brandMessages(value: any): any {
  if (typeof value === 'string') return value.split(/(https?:\/\/[^\s<>"']+)/g).map(part =>
    /^https?:\/\//.test(part) ? part : part.replace(/WeKnora(?!Cloud| Cloud)/g, '德塔智能招投标系统')
  ).join('')
  if (Array.isArray(value)) return value.map(brandMessages)
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, brandMessages(item)]))
  return value
}
const messages = brandMessages({
  'zh-CN': zhCN,
  'zh-TW': zhTW,
  'en-US': enUS,
  'ru-RU': ruRU,
  'ko-KR': koKR,
  'ja-JP': jaJP
})

// User's explicit past choice wins; otherwise use the deployment default.
const savedLocale = localStorage.getItem('locale') || resolveDefaultLocale(
  window.__RUNTIME_CONFIG__?.DEFAULT_LOCALE,
  import.meta.env.VITE_DEFAULT_LOCALE,
)

const i18n = createI18n({
  legacy: false,
  locale: savedLocale,
  fallbackLocale: BUILT_IN_DEFAULT,
  globalInjection: true,
  pluralRules,
  // Some translations intentionally embed `<strong>` markup (e.g. agent step summaries).
  // We render them via v-html with our own sanitization, so silence vue-i18n's HTML warning
  // to avoid flooding the console and slowing renders during history loads.
  warnHtmlMessage: false,
  messages
})

export default i18n
