from torch.autograd import Variable
from config import run_config, data_config

import time
import numpy as np


def train(data, model, optimizers):
    """
    Function to train the model on data and update using optimizers.

    Args:
        data (DataLoader): a data loader that provides batches of sequence data
        model (LatentVariableModel): model to train
        optimizers (tuple): inference and generative optimizers respectively
    """
    inf_opt, gen_opt = optimizers
    model.train()

    out_dict = {}
    n_batches = len(data)
    n_steps = data_config['sequence_length']-1
    n_inf_iter = run_config['inference_iterations']
    assert n_inf_iter > 0, 'Number of inference iterations must be positive.'
    out_dict['free_energy']    = np.zeros((n_batches, n_inf_iter+1, n_steps))
    out_dict['cond_log_like']  = np.zeros((n_batches, n_inf_iter+1, n_steps))
    out_dict['kl_div']         = np.zeros((n_batches, n_inf_iter+1, n_steps))
    out_dict['out_log_var']    = np.zeros((n_batches, n_inf_iter+1, n_steps))
    out_dict['mean_grad']      = np.zeros((n_batches, n_inf_iter+1))
    out_dict['log_var_grad']   = np.zeros((n_batches, n_inf_iter+1))
    out_dict['inf_param_grad'] = np.zeros(n_batches)
    out_dict['gen_param_grad'] = np.zeros(n_batches)

    # loop over training examples
    for batch_ind, batch in enumerate(data):
        print('Iteration: ' + str(batch_ind) + ' of ' + str(len(data)))
        # re-initialize the model from the data
        batch = Variable(batch.cuda())
        model.re_init(batch[0])

        # clear all of the gradients
        inf_opt.zero_stored_grad(); inf_opt.zero_current_grad()
        gen_opt.zero_stored_grad(); gen_opt.zero_current_grad()

        batch_size = batch.data.shape[1]
        step_free_energy     = np.zeros((batch_size, n_inf_iter+1, n_steps))
        step_cond_log_like   = np.zeros((batch_size, n_inf_iter+1, n_steps))
        step_kl_div          = np.zeros((batch_size, n_inf_iter+1, n_steps))
        step_output_log_var  = np.zeros((batch_size, n_inf_iter+1, n_steps))
        step_mean_grad       = np.zeros((batch_size, n_inf_iter+1, n_steps))
        step_log_var_grad    = np.zeros((batch_size, n_inf_iter+1, n_steps))
        total_reconstruction = np.zeros(batch.data.shape)

        # the total free energy for the batch of sequences
        total_free_energy = 0.

        # loop over sequence steps
        for step_ind, step_batch in enumerate(batch[1:]):

            # set the mode to inference
            model.inference_mode()

            # clear the inference model's current gradients
            inf_opt.zero_current_grad()

            # generate a prediction
            model.generate()

            # evaluate the free energy to get gradients, errors
            # model.free_energy(step_batch).backward(retain_graph=True)
            free_energy, cond_log_like, kl = model.losses(step_batch, averaged=False)
            free_energy.mean(dim=0).backward(retain_graph=True)

            step_free_energy[:, 0, step_ind]    = free_energy.data.cpu().numpy()
            step_cond_log_like[:, 0, step_ind]  = cond_log_like.data.cpu().numpy()
            step_kl_div[:, 0, step_ind]         = kl[0].data.cpu().numpy()
            step_output_log_var[:, 0, step_ind] = model.output_dist.log_var.mean(dim=2).mean(dim=1).data.cpu().numpy()
            step_mean_grad[:, 0, step_ind]      = model.latent_levels[0].latent.approx_posterior_gradients()[0].abs().mean(dim=1).data.cpu().numpy()
            step_log_var_grad[:, 0, step_ind]   = model.latent_levels[0].latent.approx_posterior_gradients()[1].abs().mean(dim=1).data.cpu().numpy()

            # iterative inference
            for inf_it in range(n_inf_iter):
                # perform inference
                model.infer(step_batch)

                # generate a reconstruction
                model.generate()

                # evaluate the free energy to get gradients, errors
                # model.free_energy(step_batch).backward(retain_graph=True)
                free_energy, cond_log_like, kl = model.losses(step_batch, averaged=False)
                free_energy.mean(dim=0).backward(retain_graph=True)

                step_free_energy[:, inf_it+1, step_ind]    = free_energy.data.cpu().numpy()
                step_cond_log_like[:, inf_it+1, step_ind]  = cond_log_like.data.cpu().numpy()
                step_kl_div[:, inf_it+1, step_ind]         = kl[0].data.cpu().numpy()
                step_output_log_var[:, inf_it+1, step_ind] = model.output_dist.log_var.mean(dim=2).mean(dim=1).data.cpu().numpy()
                step_mean_grad[:, inf_it+1, step_ind]      = model.latent_levels[0].latent.approx_posterior_gradients()[0].abs().mean(dim=1).data.cpu().numpy()
                step_log_var_grad[:, inf_it+1, step_ind]   = model.latent_levels[0].latent.approx_posterior_gradients()[1].abs().mean(dim=1).data.cpu().numpy()

            # collect the inference model gradients into the stored gradients
            inf_opt.collect()

            # set the mode to generation
            model.generative_mode()

            # run the generative model
            model.generate()

            # evaluate the free energy, add to total
            total_free_energy += model.free_energy(step_batch)
            # free_energy, cond_log_like, kl = model.losses(step_batch, averaged=False)
            # total_free_energy += free_energy.mean(dim=0)

            # step_free_energy[:, step_ind]   = free_energy.data.cpu().numpy()
            # step_cond_log_like[:, step_ind] = cond_log_like.data.cpu().numpy()
            # step_kl_div[:, step_ind]        = kl[0].data.cpu().numpy()

            total_reconstruction[step_ind] = model.output_dist.mean.data.cpu().numpy()[:, 0]

            # form the prior on the next step
            model.step()

        if np.isnan(total_free_energy.data.cpu().numpy()):
            # if nan is encountered, stop training
            print('nan encountered during training.')
            import ipdb; ipdb.set_trace()

        # clear the generative model's current gradients
        gen_opt.zero_current_grad()

        # get the gradients (for the generative model)
        total_free_energy.backward()

        # collect the gradients into the stored gradients
        gen_opt.collect()

        out_dict['inf_param_grad'] = np.mean([grad.abs().mean().data.cpu().numpy() for grad in inf_opt.stored_grads])
        out_dict['gen_param_grad'] = np.mean([grad.abs().mean().data.cpu().numpy() for grad in gen_opt.stored_grads])

        # apply the gradients to the inference and generative models
        inf_opt.step(); gen_opt.step()

        out_dict['free_energy'][batch_ind]   = step_free_energy.mean(axis=0)
        out_dict['cond_log_like'][batch_ind] = step_cond_log_like.mean(axis=0)
        out_dict['kl_div'][batch_ind]        = step_kl_div.mean(axis=0)
        out_dict['out_log_var'][batch_ind]   = step_output_log_var.mean(axis=0)
        out_dict['mean_grad'][batch_ind]     = step_mean_grad.mean(axis=2).mean(axis=0)
        out_dict['log_var_grad'][batch_ind]  = step_log_var_grad.mean(axis=2).mean(axis=0)

    out_dict['lr'] = (inf_opt.opt.param_groups[0]['lr'], gen_opt.opt.param_groups[0]['lr'])

    return out_dict


def validate(data, model):
    """
    Function to validate the model on data and update using optimizers and schedulers.

    Args:
        data (DataLoader): a data loader that provides batches of sequence data
        model (LatentVariableModel): model to train
    """

    model.eval()

    for batch_ind, batch in enumerate(data):
        model.re_init()
        for step_ind, step_batch in enumerate(batch):
            model.infer(Variable(step_batch))
            model.generate()
            model.step()
