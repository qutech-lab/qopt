"""The Dynamics class provides the interface between the optimizer and the
actual simulation.

Classes
-------
Dynamics
    Base class.

Notes
-----
The current implementation is intended as minimal working requirement.
Especially the interface for the user is still object of discussion.

Regarding the construction of class instance at run time:
    This construction can be encapsulated by set functions working as an
    internal builder pattern.
    Or by explicit construction by the user and sharing the class instance.
    This might be extended by convenience function as in pulseoptim.

"""

from typing import Optional, Sequence
import numpy as np
import time

from qsim import cost_functions, stats, solver_algorithms

from qsim.util import needs_refactoring


class Simulator(object):
    """
    The Dynamics class provides the interface for the Optimizer class. It
    wraps the infidelity and optionally the gradient of the infidelity.

    Attributes
    ----------
    solvers : list of Sover
        Instances of the time slot computers used by the cost functions.

    cost_fktns : list of CostFunction
        Instances of the cost functions which are to be optimized.

    stats : Stats
        Performance statistics.

    TODO:
        * properly implement check method as parser
        * flags controlling how much data is saved
        * is the pulse attribute useful?
        * check attributes for duplication: should num_ctrl and num_times be
            saved at this level?

    """

    def __init__(
            self,
            solvers: Optional[
                Sequence[solver_algorithms.Solver]],
            cost_fktns: Optional[
                Sequence[cost_functions.CostFunction]],
            pulse=None, num_ctrl=None, times=None, num_times=None,
            record_performance_statistics: bool = True,
            numeric_jacobian: bool = False,
            cost_fktn_weights: Optional[Sequence[float]] = None
    ):
        """Initiate a new Dynamics class.

        Parameters
        ----------
        solvers: Solver
            This object calculates the evolution of the system under
            consideration.

        cost_fktns: List[FidelityComputer]
            These are the parameters which are optimized.

        pulse: numpy array, optional
            The initial pulse of shape (N_t, N_c) where N_t is the
            number of time steps and N_c the number of controlled parameters.

        num_ctrl: int, optional
            The number of controlled parameters N_c.

        times: numpy array or list, optional
            A one dimensional numpy array of the discrete time steps.

        num_times: int, optional
            The number of time steps N_t. Mainly for consistency checks.

        record_performance_statistics: bool
            If True, then the evaluation times of the cost functions and their
            gradients are stored.

        cost_fktn_weights: list of float, optional
            The cost functions are multiplied with these weights during the
            optimisation.

        """
        self._num_ctrl = num_ctrl
        self._num_times = num_times
        self._pulse = pulse
        self._times = times

        self.tslot_comps = solvers
        self.cost_fktns = cost_fktns

        if record_performance_statistics:
            self.stats = stats.OptimizationStatistics()
        else:
            self.stats = None

        self.numeric_jacobian = numeric_jacobian
        self.cost_fktn_weights = cost_fktn_weights
        if self.cost_fktn_weights is not None:
            if len(self.cost_fktn_weights) == 0:
                self.cost_fktn_weights = None
            elif not len(self.cost_fktns) == len(self.cost_fktn_weights):
                raise ValueError('A cost function weight must be specified for'
                                 'each cost function or for none at all.')

    @property
    def pulse(self):
        return self._pulse

    @pulse.setter
    def pulse(self, new_pulse):
        """Sets the pulse and the corresponding attributes accordingly. """
        if new_pulse is not None:
            self._num_times, self._num_ctrl = self._pulse.shape
        self._pulse = new_pulse

    @needs_refactoring
    def check(self):
        """ Verifies the shape of the time steps and the pulse. """
        if self._times.size != self._num_times:
            raise ValueError(
                'There must be self.num_times values in self.times!')

        if self._pulse.shape != (self._num_times, self._num_ctrl):
            raise ValueError(
                'The shape of self.pulse does not fit to the number of times'
                ' and control amplitudes!')

    @property
    def cost_indices(self):
        cost_indices = []
        for cost_fktn in self.cost_fktns:
            cost_indices += cost_fktn.index
        return cost_indices

    def wrapped_cost_functions(self, pulse=None):
        """
        Wraps the cost functions of the fidelity computer.

        This function coordinates the complete simulation including the
        application of the transfer function, the execution of the time
        slot computer and the evaluation of the actual cost functions.

        Parameters
        ----------
        pulse: numpy array optional
            If no pulse is specified the cost function is evaluated for the
            attribute pulse.

        Returns
        -------
        costs: numpy array
            Array of costs (i.e. infidelities).

        costs_indices: list of str
            Names of the costs.

        """
        if pulse is None:
            pulse = self.pulse

        for tslot_comp in self.tslot_comps:
            tslot_comp.set_ctrl_amps(pulse)

        costs = []

        if self.stats:
            self.stats.cost_func_eval_times.append([])
            for i, cost_fktn in enumerate(self.cost_fktns):
                t_start = time.time()
                cost = cost_fktn.costs()
                if self.cost_fktn_weights is not None:
                    cost *= self.cost_fktn_weights[i]
                t_end = time.time()
                self.stats.cost_func_eval_times[-1].append(t_end - t_start)

                if hasattr(cost, "__len__"):
                    costs.append(cost)
                else:
                    costs.append(cost.reshape(1))
            costs = np.concatenate(costs, axis=0)
        else:
            for i, cost_fktn in enumerate(self.cost_fktns):
                cost = cost_fktn.costs()
                if self.cost_fktn_weights is not None:
                    cost *= self.cost_fktn_weights[i]
                if hasattr(cost, "__len__"):
                    costs.append(cost)
                else:
                    costs.append(cost.reshape(1))
            costs = np.concatenate(costs, axis=0)

        return np.asarray(costs)

    def wrapped_jac_function(self, pulse=None):
        """
        Wraps the gradient calculation functions of the fidelity computer.

        Parameters
        ----------
        pulse: numpy array, optional
            shape: (num_t, num_ctrl) If no pulse is specified the cost function
            is evaluated for the attribute pulse.

        Returns
        -------
        jac: numpy array
            Array of gradients of shape (num_t, num_func, num_amp).
        """

        if self.numeric_jacobian:
            return self.numeric_gradient(pulse=pulse)

        if pulse is None:
            pulse = self.pulse

        for tslot_comp in self.tslot_comps:
            tslot_comp.set_ctrl_amps(pulse)

        jacobians = []

        record_evaluation_times = bool(self.stats)

        if record_evaluation_times:
            self.stats.grad_func_eval_times.append([])

        for i, cost_fktn in enumerate(self.cost_fktns):
            if record_evaluation_times:
                t_start = time.time()
            jac_u = cost_fktn.grad()
            if self.cost_fktn_weights is not None:
                jac_u *= self.cost_fktn_weights[i]

            # if the cost function is scalar, an extra dimension is inserted
            if len(jac_u.shape) == 2:
                jac_u = np.expand_dims(jac_u, axis=1)

            # apply the chain rule to the derivatives
            jac_x = cost_fktn.t_slot_comp.amplitude_function.gradient_chain_rule(
                jac_u, cost_fktn.t_slot_comp.transfer_function(pulse))
            jac_x_transferred = \
                cost_fktn.t_slot_comp.transfer_function.gradient_chain_rule(
                    jac_x
                )
            jacobians.append(jac_x_transferred)
            if record_evaluation_times:
                t_end = time.time()
                self.stats.grad_func_eval_times[-1].append(t_end - t_start)

        # two dimensional form as required by scipy solvers
        total_jac = np.concatenate(jacobians, axis=1)

        return total_jac

    def compare_numeric_to_analytic_gradient(
            self, pulse: Optional[np.ndarray] = None,
            delta_eps: float = 1e-8,
            symmetric: bool = False
    ):
        """
        This function compares the numerical to the analytical gradient in order
        to serve as a consistency check.

        Parameters
        ----------
        pulse: array
            The pulse at which the gradient is evaluated.

        delta_eps: float
            The finite difference.

        symmetric: bool
            If True, then the finite differences are evaluated symmetrically
            around the pulse. Otherwise by forward finite differences.

        Returns
        -------
        gradient_difference_norm: float
            The matrix norm of the difference between the numeric and analytic
            gradient.

        gradient_difference_relative: float
            The relation of the aforementioned norm of the difference matrix
            and the average norm of the numeric and analytic gradient.

        """
        numeric_gradient = self.numeric_gradient(pulse=pulse,
                                                 delta_eps=delta_eps,
                                                 symmetric=symmetric)
        analytic_gradient = self.wrapped_jac_function(pulse=pulse)

        diff_norm = np.linalg.norm(numeric_gradient - analytic_gradient)
        relative_difference = 2 * diff_norm \
            / (np.linalg.norm(numeric_gradient)
               + np.linalg.norm(analytic_gradient))
        return diff_norm, relative_difference

    def numeric_gradient(
            self, pulse: Optional[np.ndarray] = None,
            delta_eps: float = 1e-8,
            symmetric: bool = False
    ) -> np.ndarray:
        """
        This function calculates the gradient numerically and analytically
        in order to serve as a consistency check.

        Parameters
        ----------
        pulse: array
            The pulse at which the gradient is evaluated.

        delta_eps: float
            The finite difference.

        symmetric: bool
            If True, then the finite differences are evaluated symmetrically
            around the pulse. Otherwise by forward finite differences.

        Returns
        -------
        gradients: array
            The gradients as numpy array of shape (n_time, n_func, n_opers).

        """
        if pulse is None:
            test_pulse = self.pulse
        else:
            test_pulse = pulse

        central_costs = self.wrapped_cost_functions(pulse=test_pulse)

        n_times, n_operators = test_pulse.shape
        n_cost_funcs = len(central_costs)

        gradients = np.zeros((n_times, n_cost_funcs, n_operators))

        for n_time in range(n_times):
            for n_operator in range(n_operators):
                delta = np.zeros_like(test_pulse)
                delta[n_time, n_operator] = delta_eps
                fwd_val = self.wrapped_cost_functions(test_pulse + delta)
                if symmetric:
                    bck_val = self.wrapped_cost_functions(test_pulse - delta)
                    gradients[n_time, :, n_operator] = (fwd_val - bck_val) / (
                            2 * delta_eps)
                else:
                    gradients[n_time, :, n_operator] = \
                        (fwd_val - central_costs) / delta_eps

        return gradients