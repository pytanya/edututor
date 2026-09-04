import { createRoot } from 'react-dom/client'
import 'katex/dist/katex.min.css'
import './index.css'
import App from './App.jsx'

// Без <StrictMode>: в dev он монтирует эффект дважды и дублирует запросы/сессии.
createRoot(document.getElementById('root')).render(<App />)
