import React, { createContext, useContext, useState, useEffect } from 'react';
import { authService } from '../services/authService';

const AuthContext = createContext();

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState({
    id: 1,
    email: "admin@example.com",
    username: "admin",
    role: "Manager",
    full_name: "Administrator"
  });
  const [loading, setLoading] = useState(false);
  const [token, setToken] = useState("mock-token-for-bypass");

  useEffect(() => {
    setLoading(false);
  }, []);

  const login = async (credentials) => {
    setUser({
      id: 1,
      email: "admin@example.com",
      username: "admin",
      role: "Manager",
      full_name: "Administrator"
    });
    setToken("mock-token-for-bypass");
    return { success: true };
  };

  const register = async (userData) => {
    setUser({
      id: 1,
      email: "admin@example.com",
      username: "admin",
      role: "Manager",
      full_name: "Administrator"
    });
    setToken("mock-token-for-bypass");
    return { success: true };
  };

  const logout = () => {
    // Keep user logged in even on logout click to avoid login screen redirection
    console.log("Logout clicked - bypassed");
  };

  const value = {
    user,
    token,
    loading,
    login,
    register,
    logout,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};
