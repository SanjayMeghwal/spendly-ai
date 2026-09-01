import { Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { BudgetsPage } from './pages/BudgetsPage'
import { CategoriesPage } from './pages/CategoriesPage'
import { DashboardPage } from './pages/DashboardPage'
import { GoalsPage } from './pages/GoalsPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { TransactionsPage } from './pages/TransactionsPage'

function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/transactions" element={<TransactionsPage />} />
        <Route path="/categories" element={<CategoriesPage />} />
        <Route path="/budgets" element={<BudgetsPage />} />
        <Route path="/goals" element={<GoalsPage />} />
      </Route>
    </Routes>
  )
}

export default App
