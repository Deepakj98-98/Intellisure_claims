import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export default function App() {
  const [file, setFile] = useState(null);
  const [isDragActive, setIsDragActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);
  const [currentStep, setCurrentStep] = useState(0);
  const [viewMode, setViewMode] = useState('formatted'); // 'formatted' | 'json'

  const fileInputRef = useRef(null);
  const stepperIntervalRef = useRef(null);

  const steps = [
    { id: 1, name: 'S3 Upload & PDF Parse', desc: 'Uploading PDF to Amazon S3 & extracting text contents' },
    { id: 2, name: 'Claim Intake Agent', desc: 'Parsing raw text into structured claim data and metadata' },
    { id: 3, name: 'Policy Validation Agent', desc: 'Checking insurance policy details, coverages, and exceptions' },
    { id: 4, name: 'Adjudication Agent', desc: 'Evaluating rules to decide approve/deny/exception status' },
    { id: 5, name: 'Audit Agent', desc: 'Performing quality assurance checks and assessing compliance' }
  ];

  // Clean up interval on unmount
  useEffect(() => {
    return () => {
      if (stepperIntervalRef.current) {
        clearInterval(stepperIntervalRef.current);
      }
    };
  }, []);

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setIsDragActive(true);
    } else if (e.type === 'dragleave') {
      setIsDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragActive(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const droppedFile = e.dataTransfer.files[0];
      if (droppedFile.type === 'application/pdf' || droppedFile.name.toLowerCase().endswith('.pdf')) {
        setFile(droppedFile);
        setError(null);
      } else {
        setError('Only PDF documents are allowed.');
      }
    }
  };

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      const selectedFile = e.target.files[0];
      if (selectedFile.type === 'application/pdf' || selectedFile.name.toLowerCase().endsWith('.pdf')) {
        setFile(selectedFile);
        setError(null);
      } else {
        setError('Only PDF documents are allowed.');
      }
    }
  };

  const triggerFileSelect = () => {
    fileInputRef.current.click();
  };

  const clearSelection = () => {
    setFile(null);
    setResults(null);
    setError(null);
    setCurrentStep(0);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file) {
      setError('Please select a file to upload.');
      return;
    }

    setLoading(true);
    setError(null);
    setResults(null);
    setCurrentStep(1);

    // Simulate progress progression to keep user engaged during LLM sequential calls
    // Step 1: ~2s, Step 2: ~4s, Step 3: ~7s, Step 4: ~10s, Step 5: ~13s
    let step = 1;
    stepperIntervalRef.current = setInterval(() => {
      if (step < 5) {
        step += 1;
        setCurrentStep(step);
      }
    }, 3000);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await axios.post(`${API_URL}/upload`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      if (stepperIntervalRef.current) {
        clearInterval(stepperIntervalRef.current);
      }
      
      setCurrentStep(6); // Completed state
      setResults(response.data);
    } catch (err) {
      if (stepperIntervalRef.current) {
        clearInterval(stepperIntervalRef.current);
      }
      console.error(err);
      const errMsg = err.response?.data?.detail || err.message || 'An unexpected error occurred during processing.';
      setError(errMsg);
      setLoading(false);
    } finally {
      setLoading(false);
    }
  };

  // Helper to dynamically render nested json values into premium looking grids/rows
  const renderDataGrid = (data) => {
    if (!data) return <p className="text-slate-400 italic">No details available</p>;
    if (typeof data !== 'object') return <p className="text-slate-200">{String(data)}</p>;
    
    // Check if array
    if (Array.isArray(data)) {
      return (
        <div className="space-y-3">
          {data.map((item, index) => (
            <div key={index} className="p-3 bg-slate-800/40 rounded border border-slate-700/50">
              {renderDataGrid(item)}
            </div>
          ))}
        </div>
      );
    }

    return (
      <div className="grid grid-cols-1 gap-2 text-sm">
        {Object.entries(data).map(([key, value]) => {
          const formattedKey = key
            .replace(/_/g, ' ')
            .replace(/([A-Z])/g, ' $1')
            .replace(/^./, (str) => str.toUpperCase())
            .trim();

          if (value === null || value === undefined) return null;

          if (typeof value === 'object') {
            return (
              <div key={key} className="mt-2 pt-2 border-t border-slate-800">
                <span className="text-xs text-insurance-400 font-semibold tracking-wider uppercase block mb-1">
                  {formattedKey}
                </span>
                <div className="pl-3 border-l border-slate-700">
                  {renderDataGrid(value)}
                </div>
              </div>
            );
          }

          // Format boolean
          const displayValue = typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);

          return (
            <div key={key} className="flex justify-between items-start py-1 border-b border-slate-800/50">
              <span className="text-slate-400 font-medium mr-4">{formattedKey}</span>
              <span className="text-slate-100 font-semibold text-right break-words max-w-[65%]">
                {displayValue}
              </span>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-50 flex flex-col">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex justify-between items-center">
          <div className="flex items-center space-x-3">
            <div className="h-10 w-10 rounded-xl bg-gradient-to-tr from-insurance-600 to-sky-400 flex items-center justify-center shadow-lg shadow-insurance-500/20">
              <span className="font-extrabold text-white text-xl">IS</span>
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight bg-gradient-to-r from-white via-slate-100 to-insurance-400 bg-clip-text text-transparent">
                IntelliSure AI
              </h1>
              <p className="text-xs text-slate-400 font-medium">Claims Exception Resolution Platform</p>
            </div>
          </div>
          
          <div className="flex items-center space-x-3">
            <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <span className="w-1.5 h-1.5 mr-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              API Connected
            </span>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10 w-full">
        {/* Intro */}
        <div className="mb-10 text-center max-w-3xl mx-auto">
          <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight mb-3">
            Accelerate Claim Resolutions with <span className="bg-gradient-to-r from-insurance-400 to-sky-400 bg-clip-text text-transparent">Multi-Agent AI</span>
          </h2>
          <p className="text-slate-400 text-base">
            IntelliSure AI leverages a pipeline of Amazon Bedrock Agents to automatically ingest, validate policies, adjudicate decisions, and run compliance audits on insurance claims in seconds.
          </p>
        </div>

        {/* Upload Container */}
        {!results && !loading && (
          <div className="max-w-2xl mx-auto">
            <form onSubmit={handleSubmit} className="space-y-6">
              <div
                onDragEnter={handleDrag}
                onDragOver={handleDrag}
                onDragLeave={handleDrag}
                onDrop={handleDrop}
                onClick={triggerFileSelect}
                className={`glassmorphism-card rounded-2xl border-2 border-dashed p-10 text-center cursor-pointer flex flex-col items-center justify-center min-h-[250px] transition-all duration-300 ${
                  isDragActive
                    ? 'border-insurance-500 bg-insurance-500/10 shadow-lg shadow-insurance-500/5'
                    : 'border-slate-700 hover:border-slate-600 bg-slate-900/30'
                }`}
              >
                <input
                  type="file"
                  ref={fileInputRef}
                  onChange={handleFileChange}
                  accept=".pdf"
                  className="hidden"
                />
                
                <div className="p-4 bg-slate-800/80 rounded-2xl border border-slate-700/60 mb-4 text-insurance-400 shadow-inner group-hover:scale-110 transition-transform">
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-10 h-10">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0l3 3m-3-3l-3 3M6.75 19.5a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
                  </svg>
                </div>

                {file ? (
                  <div className="space-y-2">
                    <p className="text-lg font-semibold text-slate-200 break-all">{file.name}</p>
                    <p className="text-xs text-slate-400">{(file.size / 1024 / 1024).toFixed(2)} MB • PDF Document</p>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        clearSelection();
                      }}
                      className="mt-2 text-xs text-rose-400 hover:text-rose-300 underline underline-offset-4"
                    >
                      Remove File
                    </button>
                  </div>
                ) : (
                  <div>
                    <p className="text-lg font-semibold text-slate-200">Drag & drop claim PDF here</p>
                    <p className="text-sm text-slate-400 mt-1">or click to browse from files</p>
                    <p className="text-xs text-slate-500 mt-4">Supported formats: PDF only (Max 10MB)</p>
                  </div>
                )}
              </div>

              {error && (
                <div className="p-4 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-sm flex items-start space-x-3">
                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5 flex-shrink-0 mt-0.5">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                  </svg>
                  <span>{error}</span>
                </div>
              )}

              <button
                type="submit"
                disabled={!file}
                className={`w-full py-4 px-6 rounded-xl font-bold transition-all duration-300 flex items-center justify-center space-x-2 shadow-lg ${
                  file
                    ? 'bg-gradient-to-r from-insurance-600 to-sky-500 hover:from-insurance-500 hover:to-sky-400 text-white shadow-insurance-500/20 active:scale-[0.99] cursor-pointer'
                    : 'bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700/50'
                }`}
              >
                <span>Analyze Exception & Resolve</span>
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
                </svg>
              </button>
            </form>
          </div>
        )}

        {/* Loading and Stepper State */}
        {loading && (
          <div className="max-w-3xl mx-auto glassmorphism rounded-2xl p-8 border border-slate-800 shadow-2xl">
            <div className="flex flex-col items-center justify-center mb-8">
              <div className="relative flex items-center justify-center mb-4">
                <div className="h-16 w-16 rounded-full border-4 border-slate-800 border-t-insurance-500 animate-spin"></div>
                <div className="absolute font-bold text-xs text-insurance-400">AI</div>
              </div>
              <h3 className="text-xl font-bold">Orchestrating AI Agents</h3>
              <p className="text-slate-400 text-sm mt-1">Please wait while the multi-agent pipeline validates the claim.</p>
            </div>

            {/* Stepper */}
            <div className="space-y-6">
              {steps.map((step) => {
                const isActive = currentStep === step.id;
                const isCompleted = currentStep > step.id;
                const isPending = currentStep < step.id;

                return (
                  <div key={step.id} className="flex items-start">
                    {/* Circle icon */}
                    <div className="flex flex-col items-center mr-4">
                      <div className={`h-8 w-8 rounded-full border flex items-center justify-center text-sm font-bold transition-all duration-300 ${
                        isCompleted
                          ? 'bg-insurance-600 border-insurance-500 text-white shadow shadow-insurance-500/20'
                          : isActive
                            ? 'bg-slate-800 border-insurance-400 text-insurance-400 animate-pulse'
                            : 'bg-slate-900 border-slate-700 text-slate-500'
                      }`}>
                        {isCompleted ? (
                          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
                            <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clipRule="evenodd" />
                          </svg>
                        ) : (
                          step.id
                        )}
                      </div>
                      {step.id !== steps.length && (
                        <div className={`w-0.5 h-10 ${isCompleted ? 'bg-insurance-600' : 'bg-slate-800'}`}></div>
                      )}
                    </div>

                    {/* Step details */}
                    <div className="flex-1 pt-1">
                      <h4 className={`text-sm font-bold transition-colors duration-300 ${
                        isActive ? 'text-insurance-400' : isCompleted ? 'text-slate-200' : 'text-slate-500'
                      }`}>
                        {step.name}
                      </h4>
                      <p className={`text-xs mt-0.5 transition-colors duration-300 ${
                        isActive ? 'text-slate-300 font-medium' : isCompleted ? 'text-slate-400' : 'text-slate-600'
                      }`}>
                        {step.desc}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Results Dashboard */}
        {results && (
          <div className="space-y-8 animate-fadeIn">
            {/* Action Bar */}
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center p-4 bg-slate-900/50 border border-slate-800 rounded-xl gap-4">
              <div>
                <div className="flex items-center space-x-2">
                  <span className="text-xs text-slate-400 font-medium">Resolution Target:</span>
                  <span className="text-sm font-bold text-slate-200">{file?.name}</span>
                </div>
                <div className="flex items-center space-x-3 mt-1">
                  <span className="text-xs text-slate-500">Processed successfully via 4 Bedrock agents</span>
                </div>
              </div>
              
              <div className="flex items-center space-x-4 w-full sm:w-auto justify-between sm:justify-end">
                <div className="bg-slate-800 p-0.5 rounded-lg border border-slate-700 flex">
                  <button
                    onClick={() => setViewMode('formatted')}
                    className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${
                      viewMode === 'formatted'
                        ? 'bg-insurance-600 text-white shadow'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Dashboard View
                  </button>
                  <button
                    onClick={() => setViewMode('json')}
                    className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${
                      viewMode === 'json'
                        ? 'bg-insurance-600 text-white shadow'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Raw payloads (JSON)
                  </button>
                </div>

                <button
                  onClick={clearSelection}
                  className="px-4 py-2 border border-slate-700 hover:border-slate-600 bg-slate-800/40 hover:bg-slate-800 text-slate-300 hover:text-white rounded-lg text-xs font-bold transition-all"
                >
                  Process New Claim
                </button>
              </div>
            </div>

            {/* Error fallback in results view */}
            {error && (
              <div className="p-4 bg-rose-500/10 border border-rose-500/20 text-rose-400 rounded-xl text-sm flex items-start space-x-3">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5 flex-shrink-0 mt-0.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                </svg>
                <span><strong>Execution Warning:</strong> {error}</span>
              </div>
            )}

            {/* Cards Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
              {/* Card 1: Claim Intake */}
              <div className="glassmorphism rounded-2xl p-6 border border-slate-800 shadow-xl flex flex-col min-h-[380px]">
                <div className="flex justify-between items-center pb-4 border-b border-slate-800 mb-6">
                  <div className="flex items-center space-x-3">
                    <div className="h-9 w-9 rounded-lg bg-sky-500/10 border border-sky-500/20 text-sky-400 flex items-center justify-center">
                      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                      </svg>
                    </div>
                    <div>
                      <h3 className="font-bold text-slate-100">Claim Intake</h3>
                      <p className="text-xs text-slate-400">Agent Intake & Extraction</p>
                    </div>
                  </div>
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-sky-500/10 text-sky-400 border border-sky-500/20">
                    Ingested
                  </span>
                </div>

                <div className="flex-1 overflow-y-auto max-h-[300px] pr-1">
                  {viewMode === 'json' ? (
                    <pre className="text-xs font-mono bg-slate-900/60 p-4 rounded-xl border border-slate-800 text-emerald-400 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(results.claim, null, 2)}
                    </pre>
                  ) : (
                    renderDataGrid(results.claim)
                  )}
                </div>
              </div>

              {/* Card 2: Policy Validation */}
              <div className="glassmorphism rounded-2xl p-6 border border-slate-800 shadow-xl flex flex-col min-h-[380px]">
                <div className="flex justify-between items-center pb-4 border-b border-slate-800 mb-6">
                  <div className="flex items-center space-x-3">
                    <div className="h-9 w-9 rounded-lg bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 flex items-center justify-center">
                      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.57-.598-3.75h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
                      </svg>
                    </div>
                    <div>
                      <h3 className="font-bold text-slate-100">Policy Validation</h3>
                      <p className="text-xs text-slate-400">Coverage & Rule Verification</p>
                    </div>
                  </div>
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                    Validated
                  </span>
                </div>

                <div className="flex-1 overflow-y-auto max-h-[300px] pr-1">
                  {viewMode === 'json' ? (
                    <pre className="text-xs font-mono bg-slate-900/60 p-4 rounded-xl border border-slate-800 text-emerald-400 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(results.policy, null, 2)}
                    </pre>
                  ) : (
                    renderDataGrid(results.policy)
                  )}
                </div>
              </div>

              {/* Card 3: Adjudication Decision */}
              <div className="glassmorphism rounded-2xl p-6 border border-slate-800 shadow-xl flex flex-col min-h-[380px]">
                <div className="flex justify-between items-center pb-4 border-b border-slate-800 mb-6">
                  <div className="flex items-center space-x-3">
                    <div className="h-9 w-9 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 flex items-center justify-center">
                      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m-3-2.818l.214.113c.83.437 1.345.897 1.345 1.448 0 1.14-.943 2.068-2.108 2.068-1.165 0-2.108-.928-2.108-2.068 0-.55.515-1.01 1.345-1.448l.214-.113zm10.455 0l.214.113c.83.437 1.345.897 1.345 1.448 0 1.14-.943 2.068-2.108 2.068-1.165 0-2.108-.928-2.108-2.068 0-.55.515-1.01 1.345-1.448l.214-.113zM12 5.25a.75.75 0 01.75-.75h.008a.75.75 0 01.75.75v.008a.75.75 0 01-.75.75H12.75a.75.75 0 01-.75-.75V5.25zM12 18.75a.75.75 0 01.75-.75h.008a.75.75 0 01.75.75v.008a.75.75 0 01-.75.75H12.75a.75.75 0 01-.75-.75v-.008z" />
                      </svg>
                    </div>
                    <div>
                      <h3 className="font-bold text-slate-100">Adjudication Decision</h3>
                      <p className="text-xs text-slate-400">Payment & Coverage Adjudicator</p>
                    </div>
                  </div>
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20">
                    Adjudicated
                  </span>
                </div>

                <div className="flex-1 overflow-y-auto max-h-[300px] pr-1">
                  {viewMode === 'json' ? (
                    <pre className="text-xs font-mono bg-slate-900/60 p-4 rounded-xl border border-slate-800 text-emerald-400 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(results.decision, null, 2)}
                    </pre>
                  ) : (
                    renderDataGrid(results.decision)
                  )}
                </div>
              </div>

              {/* Card 4: Audit Summary */}
              <div className="glassmorphism rounded-2xl p-6 border border-slate-800 shadow-xl flex flex-col min-h-[380px]">
                <div className="flex justify-between items-center pb-4 border-b border-slate-800 mb-6">
                  <div className="flex items-center space-x-3">
                    <div className="h-9 w-9 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 flex items-center justify-center">
                      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-5 h-5">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M10.125 2.25h3.75a2.25 2.25 0 012.25 2.25v.75h2.25A2.25 2.25 0 0121 7.5v11.25A2.25 2.25 0 0118.75 21H5.25A2.25 2.25 0 013 18.75V7.5A2.25 2.25 0 015.25 5.25h2.25v-.75a2.25 2.25 0 012.25-2.25z" />
                      </svg>
                    </div>
                    <div>
                      <h3 className="font-bold text-slate-100">Audit Summary</h3>
                      <p className="text-xs text-slate-400">QA Auditor & Compliance</p>
                    </div>
                  </div>
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    Audited
                  </span>
                </div>

                <div className="flex-1 overflow-y-auto max-h-[300px] pr-1">
                  {viewMode === 'json' ? (
                    <pre className="text-xs font-mono bg-slate-900/60 p-4 rounded-xl border border-slate-800 text-emerald-400 overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(results.audit, null, 2)}
                    </pre>
                  ) : (
                    renderDataGrid(results.audit)
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-900 py-6 bg-slate-950 mt-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row justify-between items-center text-xs text-slate-500 gap-4">
          <p>© 2026 IntelliSure AI. All rights reserved.</p>
          <div className="flex space-x-6">
            <span className="hover:text-slate-400">AWS Bedrock Agents Platform</span>
            <span className="hover:text-slate-400">Compliance & Security Insured</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
