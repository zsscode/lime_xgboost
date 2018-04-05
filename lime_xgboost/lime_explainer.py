# Copyright 2018 Patrick Hall (phall@h2o.ai) and the H2O.ai team.

# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import h2o
from h2o.estimators.glm import H2OGeneralizedLinearEstimator
import numpy as np
import pandas as pd
import xgboost as xgb


class LIMEExplainer(object):

    """ Basic framework for building Local, Interpretable, Model-agnostic
    Explanations (LIMEs) for XGBoost models. Supports regression and binomial
    classification. Requires h2o, numpy, pandas, and xgboost packages.

    :ivar training_frame: Pandas DataFrame containing the row to be explained,
                          mandatory.
    :ivar X: List of XGBoost model inputs. Inputs must be numeric, mandatory.
    :ivar model: Trained XGBoost booster to be explained, mandatory.
    :ivar N: Size of LIME local, perturbed sample. Integer, default 10000.
    :ivar discretize: Whether to discretize numeric inputs before
                      fitting a local linear model. Can increase local model
                      accuracy. Boolean, default True.
    :ivar quantiles: Number of bins to create in numeric variables. Integer,
                     default 4.
    :ivar seed: Random seed for enhanced reproducibility. Integer, default
                12345.
    :ivar print_: Whether to print a table of local contributions (reason
                  codes) and plot `top_n` local contributions (reason codes).
                  Boolean, default True.
    :ivar top_n: Number of highest and lowest Local contributions (reason codes)
                 to plot. Integer, default 5.
    :ivar reason_code_values: Pandas DataFrame containing local contributions
                              (reason codes) for `model` and row to be
                              explained.
    :ivar lime_r2: R\ :sup:`2` statistic for trained local linear model, float.
    :ivar lime: Trained local linear model, H2OGeneralizedLinearEstimator.
    :ivar bins_dict: Dictionary of bins used to discretize the LIME sample.

    Reference: https://arxiv.org/abs/1602.04938

    """

    def __init__(self, training_frame=None, X=None, model=None,
                 N=None, discretize=None, quantiles=None, seed=None,
                 print_=None, top_n=None):

        # mandatory

        if training_frame is not None:
            self.training_frame = training_frame
        else:
            raise ValueError('Parameter training_frame must be defined.')

        if X is not None:
            self.X = X
        else:
            raise ValueError('Parameter X must be defined.')

        if model is not None:
            self.model = model
        else:
            raise ValueError('Parameter model must be defined.')

        # defaults

        if N is not None:
            self.N = N
        else:
            self.N = 10000

        if discretize is not None:
            self.discretize = discretize
        else:
            self.discretize = True

        if quantiles is not None:
            self.quantiles = quantiles
        else:
            self.quantiles = 4

        if seed is not None:
            self.seed = seed
        else:
            self.seed = 12345

        if print_ is not None:
            self.print_ = print_
        else:
            self.print_ = True

        if top_n is not None:
            self.top_n = top_n
        else:
            self.top_n = 5

        # internal

        self.reason_code_values = None

        self.lime_r2 = None

        self.lime = None

        self.bins_dict = {}

        h2o.no_progress()  # do not show h2o progress bars

    def _generate_local_sample(self, row):

        """ Generates a perturbed, local sample around a row of interest.

        :param row: Row of Pandas DataFrame to be explained.

        :return: Pandas DataFrame containing perturbed, local sample.

        """

        # initialize Pandas DataFrame
        sample_frame = pd.DataFrame(data=np.zeros(shape=(self.N, len(self.X))),
                                    columns=self.X)

        # generate column vectors of
        # normally distributed numeric values around mean of numeric variables
        # with std. dev. of original numeric variables
        for key, val in self.training_frame[self.X].dtypes.items():
            rs = np.random.RandomState(self.seed)
            loc = row[key]
            sd = self.training_frame[key].std()
            draw = rs.normal(loc, sd, (self.N, 1))
            sample_frame[key] = draw

        return sample_frame

    def _score_local_sample(self, local_sample):

        """ Scores the perturbed, local sample with the user-supplied XGBoost
        `model`.

        :param local_sample: perturbed, local sample generated by
                             `_generate_local_sample`.

        :return: Pandas DataFrame containing scored, perturbed, local sample.

        """

        dlocal_sample = xgb.DMatrix(local_sample)
        scored_local_sample = pd.DataFrame(self.model.predict(dlocal_sample))
        scored_local_sample.columns = ['predict']
        return pd.concat([local_sample, scored_local_sample], axis=1)

    def _calculate_distance_weights(self, row_id, scored_local_sample):

        """ Adds inverse distance weighting from row of interest to perturbed
        local sample.

        :param row_id: Row index of row to be explained in `training_frame`.
        :param scored_local_sample: Scored, perturbed, local sample generated by
               `_score_local_sample`.

        :return: Pandas DataFrame containing weighted, scored perturbed local
                 sample.

        """

        # scaling for calculating Euclidian distance
        # for the row of interest
        scaled_training_frame = (self.training_frame[self.X] -
                                 self.training_frame[self.X].mean()) \
                                / self.training_frame[self.X].std()

        row = scaled_training_frame.iloc[row_id, :][self.X]

        # scaling for calculating Euclidian distance
        # for the perturbed local sample
        scaled_scored_local_sample = scored_local_sample[self.X].copy(deep=True)
        scaled_scored_local_sample = (scaled_scored_local_sample -
                                      scaled_scored_local_sample.mean()) \
                                     / scaled_scored_local_sample.std()

        # convert to h2o and calculate distance
        row_h2o = h2o.H2OFrame(pd.DataFrame(row).T)
        scaled_scored_local_sample_h2o = \
            h2o.H2OFrame(scaled_scored_local_sample)
        distance = row_h2o.distance(scaled_scored_local_sample_h2o,
                                    measure='l2').transpose()
        distance.columns = ['distance']

        # lower distances, higher weight in LIME
        distance = distance.max() - distance

        return pd.concat([scored_local_sample, distance.as_data_frame()],
                         axis=1)

    def _discretize_numeric(self, weighted_local_sample):

        """ Conditionally discretize the inputs in the weighted,
        scored, perturbed, local sample generated by
        `_calculate_distance_weights` into `quantiles` bins.

        :param weighted_local_sample: Weighted, scored, perturbed, local
                                      sample generated by
                                      `_calculate_distance_weights`.

        :return: Pandas DataFrame containing discretized, weighted, scored,
                 perturbed, local sample.

        """

        # initialize empty dataframe to be returned
        columns = self.X + ['predict', 'distance']
        discretized_weighted_local_sample = \
            pd.DataFrame(np.zeros((self.N, len(columns))),
                         columns=columns)

        # save bins for later use and apply to current sample
        for name in self.X:
            ser, bins = pd.qcut(weighted_local_sample.loc[:, name],
                                self.quantiles,
                                retbins=True)
            discretized_weighted_local_sample.loc[:, name] = ser
            self.bins_dict[name] = bins

        # fill in remaining columns
        discretized_weighted_local_sample.loc[:, ['predict', 'distance']] = \
            weighted_local_sample.loc[:, ['predict', 'distance']]

        return discretized_weighted_local_sample

    def _regress(self, weighted_local_sample):

        """ Train local linear model using h2o.

        :param weighted_local_sample: Weighted, scored, perturbed local
                                      sample generated by
                                      `_calculate_distance_weights` OR weighted,
                                      scored, perturbed, and discretized local
                                      sample generated by
                                      `_discretize_numeric`.

        :return: Trained local linear model as H2OGeneralizedLinearEstimator.

        """

        # initialize
        lime = H2OGeneralizedLinearEstimator(lambda_search=True,
                                             weights_column='distance',
                                             seed=self.seed)
        # train
        weighted_local_sample_h2o = h2o.H2OFrame(weighted_local_sample)
        lime.train(x=self.X, y='predict',
                   training_frame=weighted_local_sample_h2o)

        # r2
        self.lime_r2 = lime.r2()
        print('\nLocal GLM R-square: %.2f' % self.lime_r2)

        return lime

    def _plot_local_contrib(self, row_h2o):

        """ Prints local contributions (reason codes) as Pandas DataFrame
        and plots local contributions (reason codes) in a bar chart.

        :param row_h2o: Row to be explained as an H2OFrame.

        :return: Local contributions (reason codes) as Pandas DataFrame.

        """

        # initialize Pandas DataFrame to store results
        local_contrib_frame = pd.DataFrame(columns=['Input',
                                                    'Local Contribution'])

        # multiply values in row by local glm coefficients to local
        # contributions (reason codes)
        for key, val in row_h2o[self.X].types.items():
            contrib = 0
            if val == 'enum':
                level = row_h2o[key][0, 0]
                name = '.'.join([str(key), str(level)])
                if name in self.lime.coef():
                    contrib = self.lime.coef()[name]
            else:
                name = key
                if name in self.lime.coef():
                    contrib = row_h2o[name][0, 0] * self.lime.coef()[name]

            # save only non-zero values
            if np.abs(contrib) > 0.0:
                local_contrib_frame = \
                    local_contrib_frame.append({'Input': name,
                                                'Local Contribution': contrib},
                                               ignore_index=True)

                # sort
                local_contrib_frame.sort_values(by='Local Contribution',
                                                inplace=True)
                local_contrib_frame.reset_index(inplace=True, drop=True)

        # plot top and bottom local contribs
        top_n_local_contrib_frame = \
            local_contrib_frame.iloc[:self.top_n,:].\
            append(local_contrib_frame.iloc[-self.top_n:, :])
        _ = top_n_local_contrib_frame.plot(x='Input',
                                           y='Local Contribution',
                                           kind='bar',
                                           title='Reason Codes',
                                           legend=False)

        return local_contrib_frame

    def explain(self, row_id):

        """ Executes lime process.

        :param row_id: The row index of the row in `training_frame` to be
                       explained.

        """

        row = self.training_frame.iloc[row_id, :]

        local_sample = self._generate_local_sample(row)

        scored_local_sample = self._score_local_sample(local_sample)

        weighted_scored_local_sample = \
            self._calculate_distance_weights(row_id,
                                             scored_local_sample)

        if self.discretize:

            discretized_weighted_local_sampled = \
                self._discretize_numeric(weighted_scored_local_sample)
            self.lime = self._regress(discretized_weighted_local_sampled)

            disc_row = pd.DataFrame(columns=self.X)
            for name in self.X:
                disc_row[name] = pd.cut(pd.Series(row[name]),
                                        bins=self.bins_dict[name])

            if self.print_:
                rc = self._plot_local_contrib(h2o.H2OFrame(disc_row))
                print(rc)

        else:

            self.lime = self._regress(weighted_scored_local_sample)
            if self.print_:
                rc = self._plot_local_contrib(h2o.H2OFrame(pd.DataFrame(row).T))
                print(rc)
