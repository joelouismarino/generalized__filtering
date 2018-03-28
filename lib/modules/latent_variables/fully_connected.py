import torch
import torch.nn as nn
from lib.distributions import Normal
from latent_variable import LatentVariable
from lib.modules.layers import FullyConnectedLayer
from lib.modules.networks import FullyConnectedNetwork


class FullyConnectedLatentVariable(LatentVariable):
    """
    A fully-connected (Gaussian) latent variable.

    Args:
        latent_config (dict): dictionary containing variable configuration
                              parameters: n_variables, n_inputs, inference_procedure
    """
    def __init__(self, latent_config):
        super(FullyConnectedLatentVariable, self).__init__(latent_config)
        self._construct(latent_config)

    def _construct(self, latent_config):
        """
        Method to construct the latent variable from the latent_config dictionary
        """
        self.inference_procedure = latent_config['inference_procedure']
        n_variables = latent_config['n_variables']
        n_inputs = latent_config['n_in']
        # approximate posterior inputs
        self.approx_post_mean = FullyConnectedLayer({'n_in': n_inputs[0],
                                                     'n_out': n_variables})
        self.approx_post_log_var = FullyConnectedLayer({'n_in': n_inputs[0],
                                                        'n_out': n_variables})
        if self.inference_procedure != 'direct':
            self.approx_post_mean_gate = FullyConnectedLayer({'n_in': n_inputs[0],
                                                              'n_out': n_variables,
                                                              'non_linearity': 'sigmoid'})
            self.approx_post_log_var_gate = FullyConnectedLayer({'n_in': n_inputs[0],
                                                                 'n_out': n_variables,
                                                                 'non_linearity': 'sigmoid'})
        # prior inputs
        self.prior_mean = FullyConnectedLayer({'n_in': n_inputs[1],
                                               'n_out': n_variables})
        self.prior_log_var = FullyConnectedLayer({'n_in': n_inputs[1],
                                                  'n_out': n_variables})
        # distributions
        self.approx_post = Normal()
        self.prior = Normal()
        self.approx_post.re_init()
        self.prior.re_init()

    def infer(self, input):
        """
        Method to perform inference.

        Args:
            input (Tensor): input to the inference procedure
        """
        approx_post_mean = self.approx_post_mean(input)
        approx_post_log_var = self.approx_post_log_var(input)
        if self.inference_procedure == 'direct':
            self.approx_post.mean = approx_post_mean
            self.approx_post.log_var = approx_post_log_var
        else:
            approx_post_mean_gate = self.approx_post_mean_gate(input)
            self.approx_post.mean = approx_post_mean_gate * self.approx_post.mean.detach() \
                                    + (1 - approx_post_mean_gate) * approx_post_mean
            approx_post_log_var_gate = self.approx_post_log_var_gate(input)
            self.approx_post.log_var = approx_post_log_var_gate * self.approx_post.log_var.detach() \
                                       + (1 - approx_post_log_var_gate) * approx_post_log_var
        return self.approx_post.sample(resample=True)

    def generate(self, input, gen, n_samples):
        """
        Method to generate, i.e. run the model forward.

        Args:
            input (Tensor): input to the generative procedure
            gen (boolean): whether to sample from approximate poserior (False) or
                            the prior (True)
            n_samples (int): number of samples to draw
        """
        if input is not None:
            b, s, n = input.data.shape
            input = input.view(b * s, n)
            self.prior.mean = self.prior_mean(input).view(b, s, -1)
            self.prior.log_var = self.prior_log_var(input).view(b, s, -1)
        if gen:
            return self.prior.sample(n_samples, resample=True)
        return self.approx_post.sample(n_samples, resample=True)

    # def kl_divergence(self, analytical=False):
    #     if analytical:
    #         pass
    #     else:
    #         post_log_prob = self.posterior.log_prob(self.posterior.sample())
    #         prior_log_prob =  self.prior.log_prob(self.posterior.sample())
    #         return post_log_prob - prior_log_prob
    #
    # def error(self, averaged=True, normalized=False):
    #     sample = self.posterior.sample()
    #     n_samples = sample.data.shape[1]
    #     prior_mean = self.prior.mean.detach()
    #     err = sample - prior_mean[:n_samples]
    #     if normalized:
    #         prior_log_var = self.prior.log_var.detach()
    #         err /= torch.exp(prior_log_var + 1e-7)
    #     if averaged:
    #         err = err.mean(dim=1)
    #     return err

    def re_init(self):
        """
        Method to reinitialize the approximate posterior and prior over the variable.
        """
        self.re_init_approx_posterior()
        self.prior.re_init()

    def re_init_approx_posterior(self):
        """
        Method to reinitialize the approximate posterior.
        """
        mean = self.prior.mean.data.clone().mean(dim=1)
        log_var = self.prior.log_var.data.clone().mean(dim=1)
        self.approx_post.re_init(mean, log_var)

    def step(self):
        """
        Method to step the latent variable forward in the sequence.
        """
        pass

    def inference_parameters(self):
        """
        Method to obtain inference parameters.
        """
        params = []
        params.extend(list(self.approx_post_mean.parameters()))
        params.extend(list(self.approx_post_log_var.parameters()))
        if self.inference_procedure != 'direct':
            params.extend(list(self.approx_post_mean_gate.parameters()))
            params.extend(list(self.approx_post_log_var_gate.parameters()))
        return params

    def generative_parameters(self):
        """
        Method to obtain generative parameters.
        """
        params = []
        params.extend(list(self.prior_mean.parameters()))
        params.extend(list(self.prior_log_var.parameters()))
        return params

    def approx_posterior_parameters(self):
        return [self.approx_post.mean.detach(), self.approx_post.log_var.detach()]

    def approx_posterior_gradients(self):
        assert self.approx_post.mean.grad is not None, 'Approximate posterior gradients are None.'
        grads = [self.approx_post.mean.grad.detach()]
        grads += [self.approx_post.log_var.grad.detach()]
        for grad in grads:
            grad.volatile = False
        return grads
