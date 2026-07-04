export default function LoadingSpinner({ size = 'md', text = null }) {
  const sizeClasses = {
    sm: 'w-4 h-4',
    md: 'w-8 h-8',
    lg: 'w-12 h-12',
  };

  return (
    <div className="flex flex-col items-center justify-center gap-3 py-8">
      <div className={`${sizeClasses[size]} border-2 border-dark-600 border-t-brand-500 rounded-full animate-spin`} />
      {text && <p className="text-dark-400 text-sm">{text}</p>}
    </div>
  );
}
