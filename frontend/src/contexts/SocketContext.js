import React, { createContext, useContext, useEffect, useState } from 'react';
import { io } from 'socket.io-client';
import { useAuth } from './AuthContext';
import { useSnackbar } from 'notistack';

const SocketContext = createContext();

export const useSocket = () => {
  const context = useContext(SocketContext);
  if (!context) {
    throw new Error('useSocket must be used within a SocketProvider');
  }
  return context;
};

export const SocketProvider = ({ children }) => {
  const [socket, setSocket] = useState(null);
  const [connected, setConnected] = useState(false);
  const [alerts, setAlerts] = useState([]);
  const { user, token } = useAuth();
  const { enqueueSnackbar } = useSnackbar();

  useEffect(() => {
    if (user && token) {
      // Initialize WebSocket connection
      const apiURL = process.env.REACT_APP_API_URL || 'http://localhost:8000';
      const socketURL = apiURL.replace(/^http/, 'ws');
      const newSocket = io(socketURL, {
        auth: {
          token: token,
        },
        transports: ['websocket'],
      });

      newSocket.on('connect', () => {
        console.log('WebSocket connected');
        setConnected(true);
      });

      newSocket.on('disconnect', () => {
        console.log('WebSocket disconnected');
        setConnected(false);
      });

      newSocket.on('alert', (alertData) => {
        console.log('New alert received:', alertData);
        
        // Add alert to state
        setAlerts(prev => [alertData, ...prev.slice(0, 99)]); // Keep last 100 alerts
        
        // Show notification
        enqueueSnackbar(
          `${alertData.title}: ${alertData.message}`,
          {
            variant: alertData.priority === 'HIGH' ? 'error' : 'warning',
            autoHideDuration: alertData.priority === 'HIGH' ? 10000 : 5000,
          }
        );
      });

      newSocket.on('camera_status', (data) => {
        console.log('Camera status update:', data);
        // Handle camera status updates
      });

      newSocket.on('shelf_status', (data) => {
        console.log('Shelf status update:', data);
        // Handle shelf status updates
      });

      setSocket(newSocket);

      return () => {
        newSocket.close();
      };
    }
  }, [user, token, enqueueSnackbar]);

  const sendMessage = (event, data) => {
    if (socket && connected) {
      socket.emit(event, data);
    }
  };

  const clearAlerts = () => {
    setAlerts([]);
  };

  const markAlertAsRead = (alertId) => {
    setAlerts(prev => 
      prev.map(alert => 
        alert.id === alertId ? { ...alert, read: true } : alert
      )
    );
  };

  const value = {
    socket,
    connected,
    alerts,
    sendMessage,
    clearAlerts,
    markAlertAsRead,
  };

  return (
    <SocketContext.Provider value={value}>
      {children}
    </SocketContext.Provider>
  );
};
