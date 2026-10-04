import { useCallback, useEffect, useMemo, useState } from 'react'
import { clearToken, getCurrentUser, getStoredToken, loginUser, registerUser, storeToken } from '../services/api'
import { AuthContext } from './context'

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(() => Boolean(getStoredToken()))

  const logout = useCallback(() => {
    clearToken()
    setUser(null)
  }, [])

  const refreshUser = useCallback(async () => {
    if (!getStoredToken()) {
      setUser(null)
      return null
    }
    try {
      const current = await getCurrentUser()
      setUser(current)
      return current
    } catch (error) {
      logout()
      throw error
    }
  }, [logout])

  const login = useCallback(async (credentials) => {
    const response = await loginUser(credentials)
    storeToken(response.access_token)
    setUser(response.user)
    return response.user
  }, [])

  const register = useCallback(async (details) => {
    await registerUser(details)
    return login({ email: details.email, password: details.password })
  }, [login])

  useEffect(() => {
    let active = true
    const onExpired = () => setUser(null)
    window.addEventListener('citadel:session-expired', onExpired)
    if (getStoredToken()) {
      getCurrentUser()
        .then((current) => { if (active) setUser(current) })
        .catch(() => clearToken())
        .finally(() => { if (active) setLoading(false) })
    }
    return () => {
      active = false
      window.removeEventListener('citadel:session-expired', onExpired)
    }
  }, [refreshUser])

  const value = useMemo(() => ({
    user,
    currentUser: user,
    isAuthenticated: Boolean(user),
    loading,
    login,
    logout,
    register,
    refreshUser,
  }), [user, loading, login, logout, register, refreshUser])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
