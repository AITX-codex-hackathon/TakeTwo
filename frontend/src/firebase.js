import { initializeApp } from "firebase/app";
import { getAuth, GoogleAuthProvider } from "firebase/auth";

const firebaseConfig = {
  apiKey: "AIzaSyBVWQ8k5ATx7QL_23ILjYlQbFjFkRW6kcQ",
  authDomain: "take-two-86c3d.firebaseapp.com",
  projectId: "take-two-86c3d",
  storageBucket: "take-two-86c3d.firebasestorage.app",
  messagingSenderId: "341326017967",
  appId: "1:341326017967:web:aaf455f9277354802eab5a",
  measurementId: "G-WY64S7D6GT"
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
export const googleProvider = new GoogleAuthProvider();
