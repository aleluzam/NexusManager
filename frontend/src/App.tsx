import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import Layout from './components/Layout'
import HeroSection from './components/HeroSection'
import Features from './components/Features'
import Footer from './components/Footer'
import AuthPage from './components/auth/AuthPage'
import Dashboard from './components/Dashboard'
import ProtectedRoute from './components/ProtectedRoute'
import './App.css'

function LandingPage() {
  return (
    <div className="landing">
      <main className="landing-main">
        <HeroSection />
        <Features />
      </main>
      <Footer />
    </div>
  )
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Layout raíz: la Navbar es GLOBAL y persistente en todas las
              rutas (landing, /auth, /dashboard y futuras). */}
          <Route element={<Layout />}>
            <Route path="/" element={<LandingPage />} />
            <Route path="/auth" element={<AuthPage />} />
            <Route
              path="/dashboard"
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              }
            />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}

export default App
