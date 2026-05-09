import React, { useState } from "react";
import { Mail, Lock, UserCircle, Film, ArrowRight } from "lucide-react";
import { auth } from "../firebase";
import { signInWithEmailAndPassword, createUserWithEmailAndPassword } from "firebase/auth";

export default function Login({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSignUp, setIsSignUp] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (email && password) {
      try {
        if (isSignUp) {
          await createUserWithEmailAndPassword(auth, email, password);
        } else {
          await signInWithEmailAndPassword(auth, email, password);
        }
        onLogin();
      } catch (err) {
        setError(err.message.replace("Firebase: ", ""));
      }
    }
  };

  const handleGuest = () => {
    onLogin(); // Simulate guest login
  };

  return (
    <div className="login-container">

      {/* Animated Background Elements */}
      <div className="bg-shape shape1"></div>
      <div className="bg-shape shape2"></div>
      <div className="bg-shape shape3"></div>

      <div className="login-card">
        <div className="login-header">
          <div className="logo-icon-wrapper">
            <Film size={32} className="logo-icon" />
          </div>
          <h2>{isSignUp ? "Create Account" : "Welcome to Take Two"}</h2>
          <p>{isSignUp ? "Sign up to start editing your videos" : "Sign in to edit your videos"}</p>
        </div>

        {error && <div className="error-message">{error}</div>}

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="input-group">
            <Mail className="input-icon" size={20} />
            <input
              type="email"
              placeholder="Email address"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          
          <div className="input-group">
            <Lock className="input-icon" size={20} />
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          <button type="submit" className="btn-primary login-btn">
            {isSignUp ? "Sign Up" : "Sign In"}
          </button>
        </form>

        <div className="auth-toggle">
          <p>
            {isSignUp ? "Already have an account?" : "Don't have an account?"}
            <button type="button" className="toggle-btn" onClick={() => setIsSignUp(!isSignUp)}>
              {isSignUp ? "Sign In" : "Sign Up"}
            </button>
          </p>
        </div>

        <div className="divider">
          <span>or</span>
        </div>

        <button type="button" className="btn-guest" onClick={handleGuest}>
          <UserCircle size={20} />
          <span>Continue as Guest</span>
          <ArrowRight size={18} className="arrow-icon" />
        </button>
      </div>
    </div>
  );
}
