import { io } from "https://cdn.socket.io/4.7.5/socket.io.esm.min.js";


export const socket = io({
      auth:                 {},
      reconnection:         true,
      reconnectionDelay:    3000,
      reconnectionAttempts: 10
    });